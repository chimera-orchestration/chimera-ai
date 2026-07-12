"""Action logging — every action lands in the workspace's JSON-lines log.

One sink, one file: ``<workspace>/state/log.jsonl``, gitignored. Each line is a flat JSON
object ``{time, pid, command?, level, message?, **extra}`` — everything bound on the record is
included (only the ``line`` scratch is dropped), so any ``logger.bind(...)`` anywhere in the
codebase surfaces without touching this format.

A CLI action frames itself with a start/end pair sharing a ``pid`` (one process per
invocation, so ``pid`` ties them — and any lines logged in between — together):

- **start** ``{command, phase: "start", function, params}`` — the canonical command path, the
  dotted path of the pure function it runs, and the parsed params.
- **end** ``{command, phase: "end", duration_ms}`` — on a crash ``level`` is ``ERROR`` with
  ``error`` + ``traceback``; on an expected :class:`~chimera.config.UserError` it is ``ERROR``
  with ``error`` but *no* traceback (a known-bad input, not a crash).

Code between the pair logs freely — e.g. ``logger.bind(git={'before': …, 'after': …}).info(
'worktree rm: refs')`` — and those lines carry whatever they bind plus their ``message``.
Log rotation is deferred.
"""

import json
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from chimera.config import NotInWorkspaceError
from chimera.context import resolve_workspace

if TYPE_CHECKING:
    from loguru import Record

LOG_RELPATH = Path('state') / 'log.jsonl'


def log_path(workspace: Path) -> Path:
    """The workspace's action log file."""
    return workspace / LOG_RELPATH


def _format(record: 'Record') -> str:
    """Render a record as one flat JSON line: ``{time, pid, command?, level, message?, **extra}``.

    Used as loguru's ``format`` callable, so loguru emits exactly this and never appends its
    own text or traceback: str.format does not rescan the substituted value, so the JSON
    (braces and all) passes through untouched. The natives loguru also records (elapsed,
    thread, file, function, line, module, name) live outside ``extra`` and we just don't read
    them.
    """
    extra = {key: value for key, value in record['extra'].items() if key != 'line'}
    payload: dict[str, object] = {'time': record['time'].isoformat(), 'pid': record['process'].id}
    if 'command' in extra:
        payload['command'] = extra.pop('command')  # leads, ahead of level — it frames the line
    payload['level'] = record['level'].name
    if record['message']:
        payload['message'] = record['message']
    payload.update(extra)  # phase, function, params, duration_ms, git, … — anything bound
    exc = record['exception']
    if exc is not None and exc.type is not None:
        payload['error'] = f'{exc.type.__name__}: {exc.value}'
        payload['traceback'] = ''.join(
            traceback.format_exception(exc.type, exc.value, exc.traceback)
        )
    record['extra']['line'] = json.dumps(payload, default=str)
    return '{extra[line]}\n'


def configure() -> None:
    """Point loguru at the current workspace's action log (one sink, one file).

    The sink records everything from DEBUG up, so the git command trace (see ``chimera.git``)
    persists alongside the action frames. Outside any workspace (e.g. ``ch init`` before one
    exists) there's no log file: the sinks are still cleared — loguru's default stderr sink
    would otherwise spew the DEBUG trace at the console — and nothing is logged anywhere.
    """
    logger.remove()
    try:
        workspace = resolve_workspace(Path.cwd())
    except NotInWorkspaceError:
        return
    logger.add(log_path(workspace), format=_format, level='DEBUG')


def log_start(command: str, function: str, params: dict[str, object]) -> float:
    """Log a CLI action starting: its command path, the dotted ``function`` it runs, and the
    parsed ``params``. Returns a ``perf_counter`` reading to hand back to :func:`log_finish`,
    :func:`log_failure` or :func:`log_user_error`."""
    logger.bind(command=command, phase='start', function=function, params=params).info('')
    return time.perf_counter()


def log_finish(command: str, started: float) -> None:
    """Log a CLI action finishing cleanly, with how long it took."""
    logger.bind(command=command, phase='end', duration_ms=_elapsed_ms(started)).info('')


def log_failure(command: str, started: float) -> None:
    """Log a CLI action that crashed, with its full traceback. Call from an ``except`` block —
    loguru reads the live exception."""
    logger.opt(exception=True).bind(
        command=command, phase='end', duration_ms=_elapsed_ms(started)
    ).error('')


def log_user_error(command: str, started: float, error: Exception) -> None:
    """Log a CLI action that ended on an expected fault: ERROR with the ``error`` message but no
    traceback (a known-bad input, not a crash)."""
    logger.bind(
        command=command,
        phase='end',
        duration_ms=_elapsed_ms(started),
        error=f'{type(error).__name__}: {error}',
    ).error('')


def _elapsed_ms(started: float) -> float:
    """Milliseconds since ``started`` (a ``perf_counter`` reading), rounded for legibility."""
    return round((time.perf_counter() - started) * 1000, 3)
