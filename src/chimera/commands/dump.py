"""``ch dump`` — a debug snapshot for chasing hook/environment problems.

Wired temporarily as a harness hook (a ``hooks.json`` entry pointed at ``ch dump
<event-name>``) or run by hand, it lands one log line carrying everything a human or
agent needs to see what the process actually saw: cwd, pid/ppid, the ancestor process
chain, argv, the full environment, and any stdin payload (a hook's raw JSON, kept as
text — decode it as needed when reading the log back). ``caller`` and the timestamp
ride every line already (see ``chimera.logging``), so they aren't captured again here.

``pid``/``ppid`` are of ``ch dump`` itself — a harness typically runs a hook command
through an intermediate shell (``sh -c "ch dump …"``), so the immediate ``ppid`` is
usually that shell, not the harness that actually fired the hook. ``ancestry``
(``chimera.processes.process_ancestry``) walks the process tree from there, one entry
per ancestor (pid + command name), so the real launcher is visible without guessing
which level to trust.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path

from loguru import logger


def dump(
    context: str | None,
    cwd: Path,
    pid: int,
    ppid: int,
    ancestry: Sequence[Mapping[str, object]],
    argv: Sequence[str],
    env: Mapping[str, str],
    stdin: str | None,
) -> dict[str, object]:
    """Log one debug snapshot labelled ``context`` (a hook event name, free text, or
    ``None``), returning the same record for the CLI to optionally echo back."""
    record: dict[str, object] = {
        'cwd': str(cwd),
        'pid': pid,
        'ppid': ppid,
        'ancestry': [dict(entry) for entry in ancestry],
        'argv': list(argv),
        'env': dict(env),
        'stdin': stdin,
    }
    logger.bind(**record).info(f'dump: {context}' if context is not None else 'dump')
    return record
