from collections.abc import Mapping, Sequence
from pathlib import Path

from chimera.agent_env import running_under_ai_agent
from chimera.agents import Session
from chimera.agents.registry import AGENTS, AgentSpec
from chimera.config import UserError
from chimera.context import Scope
from chimera.dry import Dry
from chimera.worktrees import SEP


def agents() -> list[Session]:
    """Every checked agent session across all harnesses, enriched for listing.

    Stale entries ride along marked (``Session.stale``), never dropped, so a lister
    can surface them; a view wanting only the live decides through :func:`shown`.
    """
    return [session for harness in AGENTS.values() for session in harness.sessions()]


def shown(listing: list[Session], verbose: bool) -> tuple[list[Session], int]:
    """The rows a listing shows, and how many stale sessions it withheld.

    The default view keeps today's row set — live sessions only — counting the stale
    entries it withheld so the caller can end with the ``-v`` hint (terse defaults
    signpost their depth); ``verbose`` shows everything, so nothing is ever withheld.
    """
    if verbose:
        return listing, 0
    rows = [session for session in listing if session.stale is None]
    return rows, len(listing) - len(rows)


def live(worktree: Path) -> list[Session]:
    """Verified-live sessions in the worktree across every harness.

    The cleanup/refusal question is "is *any* agent live here", never "is a claude
    live here" — consumers (worktree rm, goal finish/rename) must go through this,
    not a single harness's listing.
    """
    return [session for harness in AGENTS.values() for session in harness.live(worktree)]


def refuse_restricted(spec: AgentSpec, extra: Sequence[str]) -> None:
    """Under an AI agent, refuse the harness's permission-bypass spellings in ``extra``.

    The Click-level strip (``__main__.main``) removes ``--dangerous`` itself, but the
    ``--`` passthrough tail is split off before Click parses, so it needs its own
    chokepoint — here, where every launcher (agent start/resume, goal start/adopt,
    review, chat) already passes and the spec is resolved. Refusing beats silently
    dropping: a session launched *without* the bypass its caller asked for would just
    be confusing. Adapters declare the spellings (``Agent.restricted``).
    """
    if running_under_ai_agent() and (hit := sorted(spec.agent.restricted.intersection(extra))):
        raise UserError(f'{", ".join(hit)}: not available when chimera is driven by an AI agent')


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
) -> None:
    """Reattach to ``spec``'s agent session named ``name`` (see ``Agent.resume``);
    ``env`` as on :func:`agent`."""
    refuse_restricted(spec, extra)
    dry(
        spec.agent.resume,
        worktree,
        name,
        prompt,
        extra,
        dangerous,
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


def scoped(listing: list[Session], scope: Scope, *, otherwise: Path | None) -> list[Session]:
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
