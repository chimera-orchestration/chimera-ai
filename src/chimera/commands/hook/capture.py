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
from chimera.archive import Event, Session, archive
from chimera.config import NotInWorkspaceError
from chimera.context import caller, resolve_scope
from chimera.worktrees import session_name, worktree_actor

_Axes = tuple[Path, str | None, str | None, str | None]

PRINT_ENTRYPOINT = 'sdk-cli'
"""``CLAUDE_CODE_ENTRYPOINT`` in a one-shot ``claude -p`` run (an interactive session
gets ``cli``). Undocumented but field-verified: claude stamps it into its own process
per-mode, so a ``-p`` spawned from inside a session never inherits the parent's value.
See ``knowledge/claude-session-type-signals.md`` for the full signal matrix."""


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
    model: str | None = None,
) -> None:
    """Record a starting session from a SessionStart hook. No-op outside any workspace.

    A ``resume``/``clear`` firing lands on the session's existing row (``started_at``
    kept, ``ended_at`` cleared — see :meth:`~chimera.archive.Archive.record_session`)
    and appends its lifecycle event, ``source`` as the kind. ``agent_type`` (from the
    payload) and ``entrypoint`` (``$CLAUDE_CODE_ENTRYPOINT``) decide whether the session
    is :func:`addressed`: one that isn't is still recorded — cwd, transcript and the
    location axes — but with no name and no actor, so no mail routes to it. ``model``
    (also from the payload) rides through to the archive; it's optional on the payload
    (absent on some firings, e.g. after ``/clear``) — :meth:`~chimera.archive.Archive.
    record_session` keeps the last known value rather than blanking it on an omitted one.
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
    if not mail:
        name = None
    elif project and goal and actor:
        # in a goal worktree the name follows the *actual* actor (what `agent start -a`
        # would have named it), never caller()'s agent default — resume keys on these axes
        name = session_name(project, goal, actor)
    else:
        name = caller(cwd)
    now = datetime.now(timezone.utc)
    with archive(workspace) as store:
        store.record_session(
            Session(
                platform='claude',
                native_id=session_id,
                status=source or 'running',
                started_at=now,
                manager='chimera' if session_role() is not None else 'none',
                model=model,
                name=name,
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

    A goal worktree resolves all four — the actor read from the ``<goal>@<actor>`` dir
    itself, never assumed: a reviewer's session archived as the agent's would hand
    ``agent resume`` the wrong conversation. A project dir has no goal or actor (a
    manager chat); the bare workspace has none (the captain). The address that
    distinguishes captain from manager rides the session's ``name`` (see :func:`caller`).
    """
    try:
        scope = resolve_scope(cwd)
    except NotInWorkspaceError:
        return None
    if scope.project is None or scope.goal is None:
        project = scope.project.name if scope.project is not None else None
        return scope.workspace, project, scope.goal, None
    # the goal was pinned by physically standing in worktrees/<goal>@<actor> (see
    # goal_from_worktree), so worktree_actor always resolves
    return (
        scope.workspace,
        scope.project.name,
        scope.goal,
        worktree_actor(cwd, scope.project.worktrees),
    )
