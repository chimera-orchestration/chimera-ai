"""Session capture: Claude's SessionStart/SessionEnd hooks feed the archive.

Every session on the machine is recorded — a chimera-launched agent or a raw ``claude`` you
ran yourself — with ``manager`` marking who launched it and the chimera axes resolved from
the session's ``cwd``. A session whose cwd is outside any workspace has nowhere to record to,
so it is the one no-op. The hook payload's ``session_id`` is the full UUID (verified), which
is the archive's ``native_id`` directly.

Each hook firing also appends one :class:`~chimera.archive.Event` — ``startup``/``resume``/
``clear`` from SessionStart's ``source``, ``end`` from SessionEnd — so the session row stays
the cheap summary (one row per identity, however many lives it has) while ``events`` carries
the append-only lifecycle history.
"""

from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from chimera.agent_env import session_role
from chimera.archive import Archive, Event, Session
from chimera.commands.msg.store import caller
from chimera.config import NotInWorkspaceError
from chimera.context import resolve_scope
from chimera.worktrees import AGENT

_Axes = tuple[Path, str | None, str | None, str | None]

PRINT_ENTRYPOINT = 'sdk-cli'
"""``CLAUDE_CODE_ENTRYPOINT`` in a one-shot ``claude -p`` run (an interactive session
gets ``cli``). Undocumented but field-verified: claude stamps it into its own process
per-mode, so a ``-p`` spawned from inside a session never inherits the parent's value.
See ``knowledge/claude-session-type-signals.md`` for the full signal matrix."""


def archive(workspace: Path) -> Archive:
    """The workspace's session archive, at ``state/archive.db``."""
    return Archive.open(workspace / 'state' / 'archive.db')


def addressed(agent_type: str | None, entrypoint: str | None) -> bool:
    """Whether a starting session may claim a mail address (a name, an actor).

    Sessions that aren't conversations still fire SessionStart: the ``claude agents``
    TUI pre-spawns a draft whose payload carries ``agent_type`` (as any subagent's
    does), and a one-shot ``claude -p`` (chimera's own description writers, errands)
    runs with the :data:`PRINT_ENTRYPOINT`. An address receives mail, so such a session
    must not be recorded holding one. Fails open — both signals absent keeps the
    address — because a real chat losing its mail over signal drift is the worse error.
    """
    return agent_type is None and entrypoint != PRINT_ENTRYPOINT


def session_start(
    cwd: Path,
    session_id: str,
    transcript: str,
    source: str,
    agent_type: str | None = None,
    entrypoint: str | None = None,
) -> None:
    """Record a starting session from a SessionStart hook. No-op outside any workspace.

    A ``resume``/``clear`` firing lands on the session's existing row (``started_at``
    kept, ``ended_at`` cleared — see :meth:`~chimera.archive.Archive.record_session`)
    and appends its lifecycle event, ``source`` as the kind. ``agent_type`` (from the
    payload) and ``entrypoint`` (``$CLAUDE_CODE_ENTRYPOINT``) decide whether the session
    is :func:`addressed`: one that isn't is still recorded — cwd, transcript and the
    location axes — but with no name and no actor, so no mail routes to it.
    """
    axes = _axes(cwd)
    if axes is None:
        return
    workspace, project, goal, actor = axes
    mail = addressed(agent_type, entrypoint)
    if not mail:
        logger.bind(session_id=session_id, agent_type=agent_type, entrypoint=entrypoint).info(
            'hook session-start: not a conversation, recording without a mail address'
        )
    now = datetime.now(timezone.utc)
    with archive(workspace) as store:
        store.record_session(
            Session(
                platform='claude',
                native_id=session_id,
                status=source or 'running',
                started_at=now,
                manager='chimera' if session_role() is not None else 'none',
                name=caller(cwd) if mail else None,
                cwd=cwd,
                transcript=Path(transcript),
                workspace=workspace.name,
                project=project,
                goal=goal,
                actor=actor if mail else None,
            )
        )
        store.record_event(
            Event(at=now, kind=source or 'startup', platform='claude', native_id=session_id)
        )


def session_end(cwd: Path, session_id: str, reason: str) -> None:
    """Mark a session ended from a SessionEnd hook. No-op outside any workspace.

    Appends the ``end`` event (``reason`` as detail) — unless the session was never
    recorded (its start predated the hooks), where there is no row to stitch it to.
    """
    axes = _axes(cwd)
    if axes is None:
        return
    at = datetime.now(timezone.utc)
    with archive(axes[0]) as store:
        store.end_session('claude', session_id, at=at, status=reason)
        if store.session('claude', session_id) is not None:
            store.record_event(
                Event(at=at, kind='end', detail=reason, platform='claude', native_id=session_id)
            )


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
