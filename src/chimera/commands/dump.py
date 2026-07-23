"""``ch dump`` — a debug snapshot for chasing hook/environment problems.

Wired temporarily as a harness hook (a ``hooks.json`` entry pointed at ``ch dump
<event-name>``) or run by hand, it lands one log line carrying everything a human or
agent needs to see what the process actually saw: cwd, pid/ppid, argv, the full
environment, and any stdin payload (a hook's raw JSON, kept as text — decode it as
needed when reading the log back). ``caller`` and the timestamp ride every line
already (see ``chimera.logging``), so they aren't captured again here.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path

from loguru import logger


def dump(
    context: str,
    cwd: Path,
    pid: int,
    ppid: int,
    argv: Sequence[str],
    env: Mapping[str, str],
    stdin: str | None,
) -> dict[str, object]:
    """Log one debug snapshot labelled ``context`` (a hook event name, or free text),
    returning the same record for the CLI to optionally echo back."""
    record: dict[str, object] = {
        'cwd': str(cwd),
        'pid': pid,
        'ppid': ppid,
        'argv': list(argv),
        'env': dict(env),
        'stdin': stdin,
    }
    logger.bind(**record).info(f'dump: {context}')
    return record
