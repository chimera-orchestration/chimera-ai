"""Action logging — every CLI action lands in the workspace's JSON-lines log.

One sink, one file: ``<workspace>/logs/chimera.jsonl``, gitignored. Log rotation is
deferred to a future plan; this is the scaffolding the rest of the CLI logs through.
"""

from pathlib import Path

from loguru import logger

from chimera.config import NotInWorkspaceError
from chimera.context import resolve_workspace

LOG_RELPATH = Path('logs') / 'chimera.jsonl'


def log_path(workspace: Path) -> Path:
    """The workspace's action log file."""
    return workspace / LOG_RELPATH


def configure() -> None:
    """Point loguru at the current workspace's action log (one sink, one file).

    Best-effort: an action run outside any workspace (e.g. ``ch init`` before one
    exists) has no log file to write to, so loguru's default sink is left in place —
    better than nothing.
    """
    try:
        workspace = resolve_workspace(Path.cwd())
    except NotInWorkspaceError:
        return
    logger.remove()
    logger.add(log_path(workspace), serialize=True, level='INFO')


def log_action(command: str, params: dict[str, object]) -> None:
    """Record one CLI action: its canonical command path and parsed parameters."""
    logger.bind(params=params).info(command)
