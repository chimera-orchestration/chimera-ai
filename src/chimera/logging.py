"""Action logging — every CLI action lands in the workspace's JSON-lines log.

One sink, one file: ``<workspace>/logs/chimera.jsonl``, gitignored. Log rotation is
deferred to a future plan; this is the scaffolding the rest of the CLI logs through.
"""

from pathlib import Path

from loguru import logger

from chimera.context import resolve_workspace

LOG_RELPATH = Path('logs') / 'chimera.jsonl'


def log_path(workspace: Path) -> Path:
    """The workspace's action log file."""
    return workspace / LOG_RELPATH


def configure() -> None:
    """Point loguru at the current workspace's action log (one sink, one file)."""
    workspace = resolve_workspace(Path.cwd())
    logger.remove()
    logger.add(log_path(workspace), serialize=True, level='INFO')


def log_action(command: str, params: dict[str, object]) -> None:
    """Record one CLI action: its canonical command path and parsed parameters."""
    logger.bind(params=params).info(command)
