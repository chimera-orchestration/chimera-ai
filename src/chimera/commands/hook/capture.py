"""Session capture: a harness's session-start/end hooks feed the archive.

Every session on the machine is recorded — a chimera-launched agent or a raw ``claude``
you ran yourself — with the chimera axes resolved from the session's ``cwd``. A session
whose cwd is outside any workspace has nowhere to record to, so it is the one no-op.

The payload is never read directly here. Which id names the session, whether it is a
conversation at all, and what kind of start it is are all questions only the harness can
answer, so they go through :class:`~chimera.agents.Agent` (see ``agent-docs/sessions.md``
for what those answers cost to learn). What this module owns is the *policy* on top:

**An address is claimed on evidence, never inferred from a location.** A chimera launcher
writes the address before the session exists; a branched session inherits its presumed
parent's, the only channel that survives a bridge. Everything else — a raw ``claude`` in a
goal worktree included — is recorded with its axes and no address. The axes say where a
session sat; only the address says who it is, and being somewhere was never evidence.

Each hook firing also appends one :class:`~chimera.archive.Event`, so the session row
stays the cheap current-state summary while ``events`` carries the lifecycle history.
"""

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from chimera.agents import BRANCHED, RESUME, Agent
from chimera.archive import Archive, ArchiveSession, Event, archive
from chimera.config import NotInWorkspaceError
from chimera.context import resolve_scope
from chimera.worktrees import worktree_actor

_Axes = tuple[Path, str | None, str | None, str | None]

HARNESS_VERSION_VAR = 'AI_AGENT'
"""The harness's own build stamp (``claude-code_2-1-220_agent``), recorded on every
session so "which versions have we seen?" is a query — and a session recorded under a
version ``agent-docs/sessions.md`` has never validated is itself the alarm. Stored whole:
it names the harness, its version and its mode together, and only the harness gets to say
what those mean."""

KNOWN_START_KEYS = frozenset(
    {'cwd', 'session_id', 'transcript_path', 'source', 'agent_type', 'model', 'hook_event_name'}
)
"""SessionStart payload keys the hook models (or deliberately ignores: ``hook_event_name``
just names the hook). Anything else is surfaced by :func:`unmodeled`, never dropped silently."""

KNOWN_END_KEYS = frozenset({'cwd', 'session_id', 'reason', 'hook_event_name'})
"""SessionEnd's equivalent of :data:`KNOWN_START_KEYS`."""


def unmodeled(hook: str, session_id: str, extra: Mapping[str, object]) -> None:
    """Surface payload keys the hook doesn't model — the harness may start sending
    signals (a fork's parent id, say) before chimera learns to read them, and a field
    dropped on the floor is invisible precisely when it would have mattered. One line,
    only when something was actually unmodeled; the whole values ride the bound field.
    """
    if extra:
        logger.bind(session_id=session_id, payload=dict(extra)).info(
            f'hook {hook}: unmodeled payload keys'
        )


def inherited(store: Archive, platform: str, cwd: Path, native_id: str) -> str | None:
    """The address a branched session takes over, from the parent it was split off.

    A bridge mints a brand-new id everywhere and its payload doesn't name what it forked
    from, so the parent is *presumed*: the newest session still open in the same cwd. That
    is the only channel an address can cross a bridge by — and it has to cross, or
    backgrounding a chat would silently orphan its mail. Presumption is the cost of the
    harness not saying; it's logged as such, and wrong only if two sessions were open in
    one directory at the moment of the fork.
    """
    parent = store.latest_open_session(platform, cwd, excluding=native_id)
    if parent is None or parent.address is None:
        return None
    logger.bind(session_id=native_id, parent=parent.native_id, address=parent.address).info(
        'hook session-start: branched session inherits its presumed parent address'
    )
    return parent.address


def _claimed(
    store: Archive,
    agent: Agent,
    cwd: Path,
    native_id: str,
    lifecycle: str,
    conversation: bool,
    now: datetime,
) -> tuple[str | None, str | None]:
    """The address and model this starting session is entitled to: ``(address, model)``.

    The whole of the address rule, in one place:

    - a session that isn't a conversation gets nothing, whatever else is true;
    - a **branched** one inherits its presumed parent's — the only channel across a bridge;
    - a **resume** takes nothing new, because the row it lands on already holds its claim
      (``record_session`` keeps an address rather than blanking it), and a resume is not
      fresh evidence of anything;
    - a cold start claims the launch chimera recorded for this directory, if there is one.

    What's left over is the case that used to go wrong: a raw session, in a goal worktree
    or anywhere else, matching no pending launch. It gets no address — being somewhere is
    not evidence of being someone.
    """
    if not conversation:
        return None, None
    if lifecycle == BRANCHED:
        return inherited(store, agent.platform, cwd, native_id), None
    if lifecycle == RESUME:
        return None, None
    launch = store.claim_launch(agent.platform, cwd, now=now)
    if launch is None:
        return None, None
    logger.bind(session_id=native_id, address=launch.address, cwd=str(cwd)).info(
        'hook session-start: claimed the launch chimera recorded'
    )
    return launch.address, launch.model


def session_start(agent: Agent, payload: Mapping[str, object], env: Mapping[str, str]) -> None:
    """Record a starting session from a session-start hook. No-op outside any workspace.

    ``agent`` is the harness whose hook fired; it answers which session this is
    (:meth:`~chimera.agents.Agent.identity`), whether it may hold an address
    (:meth:`~chimera.agents.Agent.addressable`) and what kind of start it is
    (:meth:`~chimera.agents.Agent.lifecycle`). Nothing here parses the payload itself.

    A ``resume`` lands on the session's existing row — ``started_at`` kept, ``ended_at``
    cleared, address preserved (see :meth:`~chimera.archive.Archive.record_session`) — and
    appends its lifecycle event. A ``branched`` one is a *new* row that inherits its
    presumed parent's address (:func:`inherited`). Anything else gets whatever address a
    launcher already recorded for it, and none if there wasn't one.
    """
    native_id = agent.identity(payload)
    unmodeled(
        'session-start', native_id, {k: v for k, v in payload.items() if k not in KNOWN_START_KEYS}
    )
    axes = _axes(Path(str(payload['cwd'])))
    if axes is None:
        return
    workspace, project, goal, actor = axes
    cwd = Path(str(payload['cwd']))
    lifecycle = agent.lifecycle(payload)
    conversation = agent.addressable(payload, env)
    if not conversation:
        logger.bind(session_id=native_id, lifecycle=lifecycle).info(
            'hook session-start: not a conversation, recording without an address'
        )
    now = datetime.now(timezone.utc)
    with archive(workspace) as store:
        address, model = _claimed(store, agent, cwd, native_id, lifecycle, conversation, now)
        store.record_session(
            ArchiveSession(
                platform=agent.platform,
                native_id=native_id,
                status=lifecycle,
                started_at=now,
                model=model or (str(m) if (m := payload.get('model')) else None),
                address=address,
                addressable=conversation,
                harness_version=env.get(HARNESS_VERSION_VAR) or None,
                cwd=cwd,
                transcript=_transcript(payload),
                workspace=workspace.name,
                project=project,
                goal=goal,
                actor=actor,
            )
        )
        store.record_event(
            Event(at=now, kind=lifecycle, platform=agent.platform, native_id=native_id)
        )


def _transcript(payload: Mapping[str, object]) -> Path | None:
    """The payload's transcript path, or ``None`` when it names none.

    Never ``Path('')``: that is ``Path('.')``, which is truthy and *exists*, so a session
    with no transcript would read as resumable and be handed to ``claude --resume`` — the
    "No conversation found" traceback this design started from. It would also fail the
    harness contract check, whose stem comparison ``Path('.')`` can never satisfy.
    """
    raw = payload.get('transcript_path')
    return Path(str(raw)) if raw else None


def session_end(
    cwd: Path, session_id: str, reason: str, extra: Mapping[str, object] | None = None
) -> None:
    """Mark a session ended from a SessionEnd hook. No-op outside any workspace.

    Appends the ``end`` event (``reason`` as detail) — unless the session was never
    recorded (its start predated the hooks), where there is no row to stitch it to.
    ``extra`` is whatever else rode the payload — logged via :func:`unmodeled`.
    """
    unmodeled('session-end', session_id, extra or {})
    axes = _axes(cwd)
    if axes is None:
        return
    at = datetime.now(timezone.utc)
    with archive(axes[0]) as store:
        if store.session('claude', session_id) is None:
            # the row was written under whatever `identity` anchored on at SessionStart,
            # which is the transcript stem — documented, and the one channel that did not
            # misbehave when the payload id diverged. An end that can't find its row is
            # therefore either a session older than the hooks, or that divergence
            # recurring; both are worth a line, and neither may pass silently, which is
            # what an UPDATE matching nothing did.
            logger.bind(session=session_id, cwd=str(cwd)).warning(
                'hook session-end: no recorded session, nothing to close'
            )
            return
        store.end_session('claude', session_id, at=at, status=reason)
        store.record_event(
            Event(at=at, kind='end', detail=reason, platform='claude', native_id=session_id)
        )


def _axes(cwd: Path) -> _Axes | None:
    """``(workspace, project, goal, actor)`` resolved from cwd, or ``None`` outside a workspace.

    Pure geography — where the session sat, never who it is. A goal worktree resolves all
    four, the actor read from the ``<goal>@<actor>`` dir itself rather than assumed; a
    project dir has no goal or actor; the bare workspace has none. Recorded for every
    session, addressed or not, because "what was running in this worktree" is a question
    worth answering about a raw ``claude`` too.
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
