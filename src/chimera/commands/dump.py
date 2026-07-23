"""``ch dump`` — a debug snapshot for chasing hook/environment problems.

Wired temporarily as a harness hook (a ``hooks.json`` entry pointed at ``ch dump
<event-name>``) or run by hand, it lands one log line carrying everything a human or
agent needs to see what the process actually saw: cwd, pid/ppid, the ancestor process
chain, argv, the full environment, and any stdin payload (a hook's raw JSON, kept as
text — decode it as needed when reading the log back). ``caller`` and the timestamp
ride every line already (see ``chimera.logging``), so they aren't captured again here.

``pid``/``ppid`` are of ``ch dump`` itself — a harness typically runs a hook command
through an intermediate shell (``sh -c "ch dump …"``), so the immediate ``ppid`` is
usually that shell, not the harness that actually fired the hook. ``ancestry`` walks
the process tree from there, one entry per ancestor (pid + command name), so the real
launcher is visible without guessing which level to trust.
"""

import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from loguru import logger

ParentInfo = Callable[[int], tuple[int, str] | None]

_MAX_ANCESTORS = 20


def ps_parent_info(pid: int) -> tuple[int, str] | None:
    """Query ``ps`` for ``pid``'s parent pid and command name; ``None`` once the
    process is gone or ``ps`` can't be asked (never raises — this is a debug tool)."""
    try:
        result = subprocess.run(
            ['ps', '-o', 'ppid=,comm=', '-p', str(pid)],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    line = result.stdout.strip()
    if result.returncode != 0 or not line:
        return None
    ppid_str, _, comm = line.strip().partition(' ')
    if not ppid_str.isdigit():
        return None
    return int(ppid_str), comm.strip()


def process_ancestry(pid: int, get_parent: ParentInfo = ps_parent_info) -> list[dict[str, object]]:
    """Walk the process tree upward from ``pid``, returning one ``{'pid', 'name'}``
    entry per ancestor (immediate parent first), stopping at pid 1, an unqueryable
    process, or ``_MAX_ANCESTORS`` levels (defends against a pathological loop)."""
    chain: list[dict[str, object]] = []
    current = pid
    for _ in range(_MAX_ANCESTORS):
        info = get_parent(current)
        if info is None:
            break
        parent_pid, name = info
        chain.append({'pid': parent_pid, 'name': name})
        if parent_pid <= 1:
            break
        current = parent_pid
    return chain


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
