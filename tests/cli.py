"""Test harness for the ``ch`` CLI and how it logs.

**Why this is its own module and not conftest.** Tests need to *import* the ``Command`` type
to annotate their ``command`` fixture parameter, and importing from ``conftest`` is a cardinal
sin (pytest owns conftest; importing it as a normal module risks a second, divergent copy). So
the harness lives here, and ``conftest`` imports *from* this module to build the fixtures.

**Why ``Command`` is an alias and not just ``testfixtures.Command``.** A Chimera log line is a
dict (see :func:`_general_entry`), not testfixtures' default ``(level, message)`` tuple, so
:class:`Run` overrides ``check``/``check_logging`` to accept dicts. But ty checks a call against
the *annotation*: a bare ``command: testfixtures.Command`` resolves to the stock tuple-typed
``Run.check`` and rejects our dicts. Binding the alias to *our* :class:`Run` (``Command[Run]``)
makes the dict-typed ``check`` the one ty sees — with no per-test annotation churn.
"""

import os
import re
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias

from testfixtures import Command as _Command
from testfixtures import LogCapture, Replacer
from testfixtures.command import AbstractRun, CheckResult
from testfixtures.comparing import compare
from testfixtures.loguru import LoguruSource
from testfixtures.mock import Mock, call
from testfixtures.outputcapture import OutputCapture
from typer._click.core import Command as ClickCommand
from typer.core import TyperGroup

from chimera.agents.claude import Claude
from chimera.logging import configure
from chimera.processes import process_create_time

# ch dashboard forces color (color=True) so it survives watch's non-tty pipe; strip it
# before comparing captured output, same as TestRender's own _strip in test_dashboard.py.
_ANSI = re.compile(r'\x1b\[[0-9;]*m')

if TYPE_CHECKING:
    from loguru import Record

# The keys the general capture drops — the nondeterministic ones we can't assert. Everything
# else (message + all bound extra: command/phase/function/params/git/…) is kept, so any new
# logging surfaces in assertions automatically. time/pid aren't bound, so the capture (which
# reads level + extra) never sees them anyway — they live only in the file sink.
_DROP = ('duration_ms',)


def _full_entry(record: 'Record') -> dict[str, object]:
    """Every line whole: its level, its message (when set) and all bound extra."""
    entry: dict[str, object] = {'level': record['level'].name, **record['extra']}
    if record['message']:
        entry['message'] = record['message']
    return entry


def _general_entry(record: 'Record') -> dict[str, object]:
    """A line's deterministic payload — everything except the timing (:data:`_DROP`)."""
    return {key: value for key, value in _full_entry(record).items() if key not in _DROP}


def action_logs(
    command: str,
    function: str,
    params: dict[str, object],
    *,
    error: str | None = None,
    middle: Sequence[dict[str, object]] = (),
) -> list[dict[str, object]]:
    """The start/end log pair a CLI action emits, as the general capture reduces it — for
    smoke assertions. Pass ``error`` for a UserError end line (ERROR + message); a crash's
    error/traceback ride the exception, not extra, so they don't appear here. A goal in
    ``params`` rides both frames, as ``LoggingCommand`` contextualizes it. ``middle`` is
    whatever the action logged between its frames (a launch, a ref mutation) — the goal
    rides those too, contextualized over the whole invoke, so it's merged in here rather
    than repeated at each call site."""
    goal = {'goal': params['goal']} if params.get('goal') else {}
    end: dict[str, object] = {'level': 'INFO', 'command': command, 'phase': 'end', **goal}
    if error is not None:
        end['level'] = 'ERROR'
        end['error'] = error
    return [
        {
            'level': 'INFO',
            'command': command,
            'phase': 'start',
            'function': function,
            'params': params,
            **goal,
        },
        *({**entry, **goal} for entry in middle),
        end,
    ]


def context_sources(
    ws: Path, role: str, pinned: Path | None = None, knowledge: Sequence[Path] = ()
) -> dict[str, list[str]]:
    """The sources map a render logs, every glob empty — override entries for matches.

    ``pinned`` is the pinned project's dir; ``knowledge`` the project dirs an unpinned
    scope still indexes knowledge for.
    """
    sources: dict[str, list[str]] = {str(ws / 'roles' / role / '*.md'): []}
    if pinned is not None:
        sources[str(pinned / 'roles' / role / '*.md')] = []
    sources[str(ws / 'principles' / '*.md')] = []
    if pinned is not None:
        sources[str(pinned / 'principles' / '*.md')] = []
    sources[str(ws / 'knowledge' / '*.md')] = []
    for project in [pinned] if pinned is not None else list(knowledge):
        sources[str(project / 'knowledge' / '*.md')] = []
    return sources


def sources_lines(sources: dict[str, list[str]]) -> list[str]:
    """The ``sources:`` block a --dry preview prints for a render's sources map."""
    return ['sources:'] + [f'  {pattern} ({len(files)})' for pattern, files in sources.items()]


def leaves(command: ClickCommand, path: str = '') -> Iterator[tuple[str, ClickCommand]]:
    """Every leaf of a Click tree as ``(canonical path, command)`` — the one notion of
    'leaf' shared by the doc pins (``test_docs``) and the role-allowlist pin
    (``test_main``), so both validate the same tree walk."""
    if isinstance(command, TyperGroup):
        for name, sub in command.commands.items():
            yield from leaves(sub, f'{path}{name} ')
    else:
        yield path.strip(), command


def capture_launches(replace: Replacer) -> list[object]:
    """Record every ``claude`` launch as ``(argv, cwd)``, letting other commands run.

    Launches go through :class:`subprocess.Popen` (see ``Claude._launch``), which git
    also uses, so only argv starting ``claude`` is intercepted — anything else runs for
    real. The stand-in answers the two things a launch asks of the process: a pid (our
    own, so its creation time reads back) and a zero exit.
    """
    calls: list[object] = []
    real_popen = subprocess.Popen

    def fake_popen(cmd: Any, *args: Any, cwd: Any = None, **kw: Any) -> Any:
        if cmd and cmd[0] == 'claude':
            calls.append((list(cmd), cwd))
            return _LaunchedProcess()
        return real_popen(cmd, *args, cwd=cwd, **kw)

    replace.in_module(subprocess.Popen, fake_popen)
    return calls


class _LaunchedProcess:
    """The minimal process :func:`capture_launches` hands back: a pid and a clean exit."""

    def __init__(self) -> None:
        self.pid = os.getpid()

    def wait(self) -> int:
        return 0


def launched(argv: Sequence[str], cwd: Path) -> dict[str, object]:
    """The ``agent: launched`` line a launch lands, as the captures reduce it.

    Deterministic because :func:`capture_launches` reports our own process, so the pid
    and its creation time are ours to compute — the same pair a real launch records of
    the harness process it spawned."""
    return {
        'level': 'INFO',
        'message': 'agent: launched',
        'pid': os.getpid(),
        'create_time': process_create_time(os.getpid()),
        'cwd': str(cwd),
        'argv': list(argv),
    }


def capture_env(replace: Replacer) -> list[object]:
    """The env overlay each launch hands the adapter (start and resume alike)."""
    envs: list[object] = []

    def launch(
        self: Claude,
        cwd: Path,
        name: str,
        prompt: str | None = None,
        extra: Sequence[str] = (),
        dangerous: bool = False,
        **kw: object,
    ) -> None:
        envs.append(kw.get('env'))

    replace.on_class(Claude.start, launch)
    replace.on_class(Claude.resume, launch)
    return envs


def general_capture() -> LogCapture:
    """Capture the deterministic payload of every INFO+ line (drops only the timing).

    DEBUG stays out: that level is the per-git-command trace (see ``chimera.git``), whose
    exact command sequences are an implementation detail no CLI assertion should pin.
    """
    return LogCapture(LoguruSource(attributes=_general_entry, level='INFO'))


def full_capture() -> LogCapture:
    """Capture every line whole (level + message + all bound extra) for the mechanism tests."""
    return LogCapture(LoguruSource(attributes=_full_entry))


class Run(AbstractRun):
    """A run of the ``ch`` CLI, tailored to how Chimera logs.

    Logging is loguru, captured via :func:`general_capture` (the deterministic payload of each
    line). The sink setup (:func:`chimera.logging.configure`) is mocked away — it's one call to
    one file, so we just assert it happened on every run rather than restating it.
    """

    @classmethod
    def setup_logging(cls) -> LogCapture:
        return general_capture()

    @classmethod
    def setup_mocks(cls, replace: Replacer) -> Mock:
        mocks = Mock()
        replace.in_module(configure, mocks.configure)
        return mocks

    @staticmethod
    def check_output(expected: str, output: OutputCapture) -> CheckResult:
        # ch dashboard forces color (chimera.commands.dashboard) so it survives watch's
        # non-tty pipe; strip it here so every command's expected output stays plain text.
        actual = _ANSI.sub('', output.captured)
        return CheckResult(
            'output', compare(expected=expected.strip(), actual=actual.strip(), raises=False)
        )

    @staticmethod
    def check_logging(expected: Sequence[object], logging: LogCapture) -> CheckResult:
        # a line is a dict, not testfixtures' default (level, message) tuple — see _general_entry.
        return CheckResult('logging', logging.check(*expected, raises=False))

    def check(self, logging: Sequence[object], output: str = '', return_code: int = 0) -> None:
        __tracebackhide__ = True
        self.check_results(
            self.check_output(output, self.output),
            self.check_return_code(return_code, self.return_code),
            self.check_logging(logging, self.logging),
            self.check_mock_calls([call.configure()], self.mocks),
        )


Command: TypeAlias = _Command[Run]
