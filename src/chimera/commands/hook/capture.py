"""Session capture: Claude's SessionStart/SessionEnd hooks feed the archive.

Every session on the machine is recorded — a chimera-launched agent or a raw ``claude`` you
ran yourself — with ``manager`` marking who launched it and the chimera axes resolved from
the session's ``cwd``. A session whose cwd is outside any workspace has nowhere to record to,
so it is the one no-op. The hook payload's ``session_id`` is the full UUID (verified), which
is the archive's ``native_id`` directly.
"""

from datetime import datetime, timezone
from pathlib import Path

from chimera.agent_env import session_role
from chimera.archive import Archive, Session
from chimera.commands.msg.store import caller
from chimera.config import NotInWorkspaceError
from chimera.context import resolve_scope
from chimera.worktrees import AGENT

_Axes = tuple[Path, str | None, str | None, str | None]


def archive(workspace: Path) -> Archive:
    """The workspace's session archive, at ``state/archive.db``."""
    return Archive.open(workspace / 'state' / 'archive.db')


def session_start(cwd: Path, session_id: str, transcript: str, source: str) -> None:
    """Record a starting session from a SessionStart hook. No-op outside any workspace."""
    axes = _axes(cwd)
    if axes is None:
        return
    workspace, project, goal, actor = axes
    with archive(workspace) as store:
        store.record_session(
            Session(
                platform='claude',
                native_id=session_id,
                status=source or 'running',
                started_at=datetime.now(timezone.utc),
                manager='chimera' if session_role() is not None else 'none',
                name=caller(cwd),
                cwd=cwd,
                transcript=Path(transcript),
                workspace=workspace.name,
                project=project,
                goal=goal,
                actor=actor,
            )
        )


def session_end(cwd: Path, session_id: str, reason: str) -> None:
    """Mark a session ended from a SessionEnd hook. No-op outside any workspace."""
    axes = _axes(cwd)
    if axes is None:
        return
    with archive(axes[0]) as store:
        store.end_session('claude', session_id, at=datetime.now(timezone.utc), status=reason)


def _axes(cwd: Path) -> _Axes | None:
    """``(workspace, project, goal, actor)`` resolved from cwd, or ``None`` outside a workspace.

    A goal worktree resolves all four (its actor is ``agent``); a project dir has no goal or
    actor (a manager chat); the bare workspace has none (the captain). The address that
    distinguishes captain from manager rides the session's ``name`` (see :func:`caller`).
    """
    try:
        scope = resolve_scope(cwd)
    except NotInWorkspaceError:
        return None
    project = scope.project.name if scope.project is not None else None
    actor = AGENT if scope.goal is not None else None
    return scope.workspace, project, scope.goal, actor
