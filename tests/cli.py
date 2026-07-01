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

from collections.abc import Sequence
from typing import TYPE_CHECKING, TypeAlias

from testfixtures import Command as _Command
from testfixtures import LogCapture, Replacer
from testfixtures.command import AbstractRun, CheckResult
from testfixtures.loguru import LoguruSource
from testfixtures.mock import Mock, call

from chimera.logging import configure

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
    command: str, function: str, params: dict[str, object], *, error: str | None = None
) -> list[dict[str, object]]:
    """The start/end log pair a CLI action emits, as the general capture reduces it — for
    smoke assertions. Pass ``error`` for a UserError end line (ERROR + message); a crash's
    error/traceback ride the exception, not extra, so they don't appear here."""
    end: dict[str, object] = {'level': 'INFO', 'command': command, 'phase': 'end'}
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
        },
        end,
    ]


def general_capture() -> LogCapture:
    """Capture the deterministic payload of every line (drops only the timing)."""
    return LogCapture(LoguruSource(attributes=_general_entry))


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
