"""Process-tree introspection shared across commands that need to know what launched
the current process (a harness typically runs a hook command through an intermediate
shell, so the immediate parent pid alone doesn't name the real launcher)."""

from collections.abc import Callable

import psutil

ParentInfo = Callable[[int], tuple[int, str] | None]

_MAX_ANCESTORS = 20


def psutil_parent_info(pid: int) -> tuple[int, str] | None:
    """``pid``'s parent pid and process name via psutil; ``None`` once the process
    or its parent is gone (never raises — callers use this for best-effort context)."""
    try:
        parent = psutil.Process(pid).parent()
        if parent is None:
            return None
        return parent.pid, parent.name()
    except psutil.Error:
        return None


def process_ancestry(
    pid: int, get_parent: ParentInfo = psutil_parent_info
) -> list[dict[str, object]]:
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
