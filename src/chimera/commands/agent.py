from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from chimera.addresses import Actor
from chimera.agent_env import ai_session
from chimera.agents import BRANCHED, AgentSession
from chimera.agents.registry import AGENTS, AgentSpec
from chimera.archive import RECONCILED, ArchiveSession, Event, PendingLaunch, archive
from chimera.config import NotInWorkspaceError, UserError
from chimera.context import Scope, resolve_workspace
from chimera.dry import Dry
from chimera.worktrees import SEP


def agents() -> list[AgentSession]:
    """Every checked agent session across all harnesses, enriched for listing.

    Stale entries ride along marked (``AgentSession.stale``), never dropped, so a lister
    can surface them; a view wanting only the live decides through :func:`shown`.
    """
    return [session for harness in AGENTS.values() for session in harness.sessions()]


def shown(listing: list[AgentSession], verbose: bool) -> tuple[list[AgentSession], int]:
    """The rows a listing shows, and how many stale sessions it withheld.

    The default view keeps today's row set — live sessions only — counting the stale
    entries it withheld so the caller can end with the ``-v`` hint (terse defaults
    signpost their depth); ``verbose`` shows everything, so nothing is ever withheld.
    """
    if verbose:
        return listing, 0
    rows = [session for session in listing if session.stale is None]
    return rows, len(listing) - len(rows)


def live(worktree: Path) -> list[AgentSession]:
    """Verified-live sessions in the worktree across every harness.

    The cleanup/refusal question is "is *any* agent live here", never "is a claude
    live here" — consumers (worktree rm, goal finish/rename) must go through this,
    not a single harness's listing.
    """
    return [session for harness in AGENTS.values() for session in harness.live(worktree)]


def stop(worktree: Path, dry: Dry = Dry(), timeout: float = 10.0) -> list[AgentSession]:
    """Stop every live agent session in the worktree, through its own harness.

    The polite kill for work that's over — a session's committed work is already on its
    branch, and anything uncommitted was the caller's to check *before* stopping. Refuses
    when the worktree itself doesn't exist (a mistyped goal or actor must never read as
    "nothing running") or when a session reports no pid (nothing to signal — a
    server-backed harness needs its own stop). Each session is stopped by the harness
    that reported it (:meth:`chimera.agents.Agent.stop`) — the harness-agnostic default
    is SIGTERM-and-wait, but a harness whose sessions need their own graceful shutdown
    (claude's background jobs — see :meth:`chimera.agents.claude.Claude.stop`) overrides
    it, so a stop actually sticks instead of being silently respawned. Under ``dry`` the
    discovery runs but nothing is stopped. Returns the sessions that were (or would be)
    stopped.
    """
    if not worktree.is_dir():
        raise UserError(f'no worktree at {worktree} — check the goal (-g) and actor (-a)')
    pairs = [
        (harness, session) for harness in AGENTS.values() for session in harness.live(worktree)
    ]
    for harness, session in pairs:
        if session.pid is None:
            raise UserError(
                f'{session.name} reports no pid — stop it from its own harness, then re-run'
            )
        dry(harness.stop, session, timeout)
    return [session for _, session in pairs]


def refuse_restricted(cwd: Path, spec: AgentSpec, extra: Sequence[str]) -> None:
    """In an AI session, refuse the harness's permission-bypass spellings in ``extra``.

    The Click-level strip (``__main__.main``) removes ``--dangerous`` itself, but the
    ``--`` passthrough tail is split off before Click parses, so it needs its own
    chokepoint — here, where every launcher (agent start/resume, goal start/adopt,
    review, chat) already passes and the spec is resolved. Refusing beats silently
    dropping: a session launched *without* the bypass its caller asked for would just
    be confusing. Adapters declare the spellings (``Agent.restricted``); the trigger is
    ``ai_session()`` — the same signal pair as the strip, so a session chimera launched
    under a markerless harness can't smuggle a bypass through the tail either.
    """
    if ai_session(cwd) and (hit := sorted(spec.agent.restricted.intersection(extra))):
        raise UserError(f'{", ".join(hit)}: not available when chimera is driven by an AI agent')


def _husks(open_rows: dict[str, ArchiveSession], forks: Sequence[str | None]) -> set[str]:
    """The still-open sessions each still-open fork left frozen behind it.

    The same presumption ``hook.capture.inherited`` makes when an address crosses a bridge
    — the newest session open in that cwd — but evaluated at the fork's own start rather
    than now, so a session that arrived *after* the fork is never mistaken for what it
    forked from. Both sides must still be open: a fork and husk that are long dead say
    nothing about who is working in the directory today.
    """
    husks: set[str] = set()
    for native_id in forks:
        fork = open_rows[native_id or '']
        parents = [
            row
            for row in open_rows.values()
            if row.cwd == fork.cwd
            and row.native_id != fork.native_id
            and row.started_at <= fork.started_at
        ]
        if parents:
            husks.add(max(parents, key=lambda row: row.started_at).native_id)
    return husks


def occupants(worktree: Path, excluding: str | None = None) -> list[AgentSession]:
    """Who is really *working* in ``worktree`` — the one definition, for every consumer.

    "Live here" is not the same question as "would clash with me here", and the gap is
    made of things the harness reports as sessions but nobody would call an occupant:

    - **non-conversations.** A ``claude agents`` browser draft and a one-shot ``claude -p``
      fire the same session hooks as a chat, and both routinely share a worktree with the
      real thing. The archive already records the harness's verdict (``addressable``), so
      that is what's consulted — not a re-derivation here.
    - **husks.** Backgrounding a session leaves the parent alive but conversationally
      frozen, registry-``busy`` until its terminal wrapper exits — observed for as long as
      35 hours. A ``branched`` session is the marker that a fork happened, and the husk it
      left is nobody's occupant — but the husk is *that fork's own presumed parent*, never
      merely a neighbour. Asking only whether the directory had ever seen a fork excused
      every later session in it forever, which silently disarmed the guard for any worktree
      a chat had ever been backgrounded in.
    - **me.** A session asking whether the worktree is free must not find itself.

    Anything unrecognised counts, deliberately: refusing to launch beside a session that
    turns out to be harmless is recoverable, while launching a second writer into one
    worktree is not.
    """
    sessions = live(worktree)
    if not sessions:
        return []
    try:
        workspace = resolve_workspace(worktree)
    except NotInWorkspaceError:
        return [s for s in sessions if s.id != excluding]
    with archive(workspace) as store:
        rows = {row.native_id: row for row in store.sessions(workspace=workspace.name)}
        open_rows = {
            row.native_id: row for row in store.sessions(workspace=workspace.name, active=True)
        }
        forks = [
            event.native_id for event in store.events(kind=BRANCHED) if event.native_id in open_rows
        ]
    husks = _husks(open_rows, forks)
    keep: list[AgentSession] = []
    for session in sessions:
        if session.id == excluding:
            continue
        row = rows.get(session.id)
        if row is not None and not row.addressable:
            logger.bind(session=session.id, cwd=str(worktree)).debug(
                'agent: not a conversation, not an occupant'
            )
            continue
        if session.id in husks:
            logger.bind(session=session.id, cwd=str(worktree)).debug(
                'agent: husk of a backgrounded session, not an occupant'
            )
            continue
        keep.append(session)
    return keep


def refuse_occupied(worktree: Path, dry: Dry = Dry()) -> None:
    """Refuse to launch into a worktree something else is already working in.

    One writer per worktree: two agents editing one checkout is how work gets lost. This
    is the exclusive-launch guard every goal-worktree launcher takes — ``ch chat`` alone
    opts out, by not calling it, since a chat deliberately sits alongside a working agent.

    It asks :func:`occupants` rather than the raw registry, so a browser draft or the husk
    of a backgrounded session no longer refuses a launch nothing is really using the
    worktree for. A harness-native start (a raw ``claude``, a browser attach) never
    reaches a launcher and so cannot be refused here at all — that is what the
    SessionStart warning is for.

    Two things it deliberately does not do. It doesn't ask about a worktree that isn't
    there: nothing can occupy a missing directory, and the launch's own error says the
    definite thing. And it stands down under ``dry``, because a preview mutates nothing —
    liveness must never be what makes a preview unavailable.
    """
    if dry.on or not worktree.is_dir():
        return
    if running := occupants(worktree):
        ids = ', '.join(f'{s.id} ({s.status})' for s in running)
        raise RuntimeError(f'an agent is already live in {worktree}: {ids} — attach or stop it')


def reconcile(workspace: Path, listing: list[AgentSession]) -> list[ArchiveSession]:
    """Close archived sessions no harness reports live any more; return the ones closed.

    A session that dies without its end hook firing — killed, crashed, its machine
    rebooted — stays open in the archive forever, and an open row outranks the closed
    ones a resume should be choosing between. The registry is the authority on what is
    running, so anything it no longer claims is over.

    Called by the listers, because that is when the answer is about to be read and a
    correction costs nothing: pure SQL, a registry query and a pid check, no model turn
    (see AGENTS.md's *No tokens for admin*). ``listing`` is the live sessions the caller
    already gathered, so the reconciliation adds no work of its own.

    **A harness that cannot be consulted is not a harness reporting nothing.** With
    ``claude`` off the PATH — a cron shell, a stripped environment — its registry query
    answers with an empty list, and closing every open row on that basis would declare
    every agent on the machine dead from a read-only lister. So the listing is only
    trusted while every harness can actually be asked.
    """
    if unavailable := [name for name, harness in AGENTS.items() if not harness.available()]:
        logger.bind(harnesses=unavailable).warning(
            'agent: harness unavailable, leaving open sessions alone'
        )
        return []
    live = {(session.id) for session in listing}
    at = datetime.now(timezone.utc)
    with archive(workspace) as store:
        closed = [row for row in store.sessions(active=True) if row.native_id not in live]
        for row in closed:
            store.end_session(row.platform, row.native_id, at=at, status=RECONCILED)
            store.record_event(
                Event(
                    at=at,
                    kind='end',
                    detail=RECONCILED,
                    platform=row.platform,
                    native_id=row.native_id,
                )
            )
    if closed:
        logger.bind(sessions=[row.native_id for row in closed]).info(
            'agent: closed sessions no harness still reports'
        )
    return closed


def record_launch(cwd: Path, address: str, spec: AgentSpec) -> None:
    """Put the launch chimera is about to make on record, so its session can claim it.

    The one place an address is *established*: written before the spawn, because neither
    launch mode lets the launcher write a complete row afterwards (a foreground launch
    blocks until the session exits; a background one is refused the chance to choose an
    id). The session's start hook binds the identity to it.

    Best-effort by design — a project standing outside any workspace has no archive to
    record to, and a launch must not fail for want of bookkeeping. Such a session simply
    starts unaddressed, exactly as a hand-launched one does.
    """
    try:
        workspace = resolve_workspace(cwd)
    except NotInWorkspaceError:
        return
    with archive(workspace) as store:
        store.record_launch(
            PendingLaunch(
                at=datetime.now(timezone.utc),
                platform=spec.agent.platform,
                cwd=cwd,
                address=address,
                model=spec.model,
            )
        )
    logger.bind(address=address, cwd=str(cwd), platform=spec.agent.platform).info(
        'agent: launching'
    )


def agent(
    worktree: Path,
    name: str,
    prompt: str | None = None,
    extra: Sequence[str] = (),
    dangerous: bool = False,
    spec: AgentSpec = AgentSpec(),
    context: Path | None = None,
    dry: Dry = Dry(),
) -> None:
    """Launch ``spec``'s agent session named ``name`` in the worktree (see ``Agent.start``)."""
    refuse_restricted(worktree, spec, extra)
    refuse_occupied(worktree, dry)
    dry(record_launch, worktree, name, spec)
    dry(
        spec.agent.start,
        worktree,
        name,
        prompt,
        extra,
        dangerous,
        model=spec.model,
        context=context,
    )


def resume_target(cwd: Path, platform: str, project: str, goal: str, actor: str) -> str | None:
    """The archived native session id ``agent resume`` resumes by, else ``None``.

    Session identity lives in the archive, and is asked for **by address**, never by the
    axes that happen to match it: a raw ``claude`` or an errand's one-shot run in the same
    worktree records those axes too, and reviving a human's private conversation under the
    agent's name is precisely what an address is for preventing. The address maps
    to its newest session — live or dead, resuming is how a dead one is revived — and
    that session's immutable native id is the resume target; the registry name is
    display-only (a rename in the harness's UI must not orphan the session). ``None`` —
    no workspace to hold an archive, an address it has never seen, or nothing left whose
    transcript still exists — falls back to resuming by name.

    Sessions whose transcript the harness has pruned are skipped: handing claude an id it
    no longer knows produced a raw "No conversation found" traceback, which is the failure
    this whole design started from.
    """
    try:
        workspace = resolve_workspace(cwd)
    except NotInWorkspaceError:
        return None
    with archive(workspace) as store:
        session = store.latest_session_for(
            project, address=str(Actor(project, goal, actor)), platform=platform, resumable=True
        )
    if session is None:
        return None
    logger.bind(
        platform=platform, native_id=session.native_id, project=project, goal=goal, actor=actor
    ).info('agent resume: archived session')
    return session.native_id


def resume(
    worktree: Path,
    name: str,
    prompt: str | None = None,
    extra: Sequence[str] = (),
    dangerous: bool = False,
    spec: AgentSpec = AgentSpec(),
    context: Path | None = None,
    dry: Dry = Dry(),
    id: str | None = None,
) -> None:
    """Revive ``spec``'s agent session — by archived ``id`` when the caller resolved
    one (see :func:`resume_target`), else by ``name`` (see ``Agent.resume``).

    Deliberately records no launch. A resume takes nothing new: the address is already on
    the session's own row and ``record_session`` coalesces it forward, which is why the
    start hook's resume branch claims nothing either. A launch record written here would
    simply go unconsumed — and an unconsumed claim is not inert, it waits out its window
    for whatever cold-starts in the directory next, which is how a passer-by would come
    to hold this agent's address and take its mail.
    """
    refuse_restricted(worktree, spec, extra)
    refuse_occupied(worktree, dry)
    dry(
        spec.agent.resume,
        worktree,
        name,
        prompt,
        extra,
        dangerous,
        id=id,
        model=spec.model,
        context=context,
    )


def scope_line(scope: Scope) -> str:
    """The banner shown above ``agent ls`` — what the list below is bounded to.

    ``scope: <project>@<goal>`` when both are pinned (mirroring the ``<project>@<goal>@<actor>``
    agent names in the rows), ``scope: <project>`` for a whole project, ``scope: all agents``
    for the unbounded global list. The stable ``scope:`` key stays greppable for agents while
    reading naturally for humans.
    """
    if scope.project is not None and scope.goal is not None:
        target = f'{scope.project.name}{SEP}{scope.goal}'
    elif scope.project is not None:
        target = scope.project.name
    else:
        target = 'all agents'
    return f'scope: {target}'


def scoped(
    listing: list[AgentSession], scope: Scope, *, otherwise: Path | None
) -> list[AgentSession]:
    """The sessions in scope: under the goal's worktrees, the project, else ``otherwise``.

    With no project pinned the fallback ``otherwise`` decides reach: ``None`` keeps every
    session (``agent ls`` is the global list), a path bounds them to it (the dashboard passes
    the workspace, so it never shows strays from elsewhere on the machine).
    """
    if scope.project is not None and scope.goal is not None:
        return [a for a in listing if in_goal(a.cwd, scope.project.worktrees, scope.goal)]
    if scope.project is not None:
        return [a for a in listing if under(a.cwd, scope.project.dir)]
    if otherwise is None:
        return listing
    return [a for a in listing if under(a.cwd, otherwise)]


def under(path: Path, root: Path) -> bool:
    """Whether path is root or a descendant of it (both resolved)."""
    path, root = path.resolve(), root.resolve()
    return path == root or root in path.parents


def in_goal(cwd: Path, worktrees: Path, goal: str) -> bool:
    """Whether cwd sits in one of goal's actor worktrees (``<goal>@<actor>``) under worktrees."""
    worktrees = worktrees.resolve()
    if not under(cwd, worktrees):
        return False
    relative = cwd.resolve().relative_to(worktrees)
    return bool(relative.parts) and relative.parts[0].startswith(f'{goal}{SEP}')
