"""Process-tree introspection shared across commands that need to know what launched
the current process (a harness typically runs a hook command through an intermediate
shell, so the immediate parent pid alone doesn't name the real launcher)."""

import psutil

_MAX_ANCESTORS = 20

CREATE_TIME_TOLERANCE = 0.001
"""Slack when matching two creation times (seconds). A false "different process"
verdict marks a healthy session dead, so the comparison absorbs storage rounding;
a reused pid is orders of magnitude further apart than this."""


def psutil_parent_info(pid: int) -> tuple[int, str] | None:
    """``pid``'s parent pid and process name via psutil; ``None`` once the process
    or its parent is gone (never raises — callers use this for best-effort context)."""
    try:
        parent = psutil.Process(pid).parent()
        if parent is None:
            return None
        return parent.pid, parent.name()
    # the only two errors Process()/.parent()/.name() can raise; TimeoutExpired is
    # wait()-only (never called here), so it's left to propagate rather than hidden
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def process_create_time(pid: int) -> float | None:
    """``pid``'s creation time (unix epoch seconds), or ``None`` when it can't be read.

    The other half of process identity: a pid alone is a slot the kernel reuses, so
    only the ``(pid, create_time)`` **pair** names one process across time. Compare
    pairs for equality — never creation time against anything else. "Created after the
    session started" is *not* evidence of staleness: a harness may hand a session a
    pooled worker claimed long after it began (see ``agent-docs/sessions.md``), so that
    inequality marks healthy sessions dead.

    ``None`` means unreadable, never dead: the process may simply belong to another
    user. Callers treat an unknown creation time as "no pair to check", not as proof.
    """
    try:
        return psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def same_process(create_time: float | None, current: float | None) -> bool:
    """Whether two creation times name the same process — the pid-reuse check.

    True when either side is unknown: an unreadable creation time is no evidence, and
    refusing to act on absent evidence would break every session whose process belongs
    to another user. Compared with :data:`CREATE_TIME_TOLERANCE` slack, since a value
    that has been round-tripped through storage need only identify the process, not
    survive bit-exact.
    """
    if create_time is None or current is None:
        return True
    return abs(create_time - current) <= CREATE_TIME_TOLERANCE


def process_ancestry(pid: int) -> list[dict[str, object]]:
    """Walk the process tree upward from ``pid``, returning one ``{'pid', 'name'}``
    entry per ancestor (immediate parent first), stopping at pid 1, an unqueryable
    process, or ``_MAX_ANCESTORS`` levels (defends against a pathological loop)."""
    chain: list[dict[str, object]] = []
    current = pid
    for _ in range(_MAX_ANCESTORS):
        info = psutil_parent_info(current)
        if info is None:
            break
        parent_pid, name = info
        chain.append({'pid': parent_pid, 'name': name})
        if parent_pid <= 1:
            break
        current = parent_pid
    return chain
