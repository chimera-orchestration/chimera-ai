from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from chimera.agent_env import ai_session
from chimera.agents import AgentSession
from chimera.agents.registry import AGENTS, AgentSpec
from chimera.archive import PendingLaunch, archive
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


def refuse_restricted(spec: AgentSpec, extra: Sequence[str]) -> None:
    """In an AI session, refuse the harness's permission-bypass spellings in ``extra``.

    The Click-level strip (``__main__.main``) removes ``--dangerous`` itself, but the
    ``--`` passthrough tail is split off before Click parses, so it needs its own
    chokepoint — here, where every launcher (agent start/resume, goal start/adopt,
    review, chat) already passes and the spec is resolved. Refusing beats silently
    dropping: a session launched *without* the bypass its caller asked for would just
    be confusing. Adapters declare the spellings (``Agent.restricted``); the trigger is
    ``ai_session()`` — the same signal pair as the strip, so a role-stamped session
    under a markerless harness can't smuggle a bypass through the tail either.
    """
    if ai_session() and (hit := sorted(spec.agent.restricted.intersection(extra))):
        raise UserError(f'{", ".join(hit)}: not available when chimera is driven by an AI agent')


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
    env: Mapping[str, str] = {},
    dry: Dry = Dry(),
) -> None:
    """Launch ``spec``'s agent session named ``name`` in the worktree (see ``Agent.start``).

    ``env`` is extra variables overlaid on the session's environment — how a launcher
    stamps role identity into it (see ``chimera.agent_env.role_env``).
    """
    refuse_restricted(spec, extra)
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
        env=env,
    )


def resume_target(cwd: Path, platform: str, project: str, goal: str, actor: str) -> str | None:
    """The archived native session id ``agent resume`` resumes by, else ``None``.

    Session identity lives in the archive: the address ``(project, goal, actor)`` maps
    to its newest session — live or dead, resuming is how a dead one is revived — and
    that session's immutable native id is the resume target; the registry name is
    display-only (a rename in the harness's UI must not orphan the session). ``None`` —
    no workspace to hold an archive, or an address it has never seen — falls back to
    resuming by name.
    """
    try:
        workspace = resolve_workspace(cwd)
    except NotInWorkspaceError:
        return None
    with archive(workspace) as store:
        session = store.latest_session_for(project, goal, actor, platform=platform)
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
    env: Mapping[str, str] = {},
    dry: Dry = Dry(),
    id: str | None = None,
) -> None:
    """Revive ``spec``'s agent session — by archived ``id`` when the caller resolved
    one (see :func:`resume_target`), else by ``name`` (see ``Agent.resume``); ``env`` as
    on :func:`agent`."""
    refuse_restricted(spec, extra)
    dry(record_launch, worktree, name, spec)
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
        env=env,
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
