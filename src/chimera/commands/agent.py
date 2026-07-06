from collections.abc import Sequence
from pathlib import Path

from chimera.agents import Session
from chimera.agents.registry import AGENTS, AgentSpec
from chimera.context import Scope
from chimera.dry import Dry
from chimera.worktrees import SEP


def agents() -> list[Session]:
    """Every live agent session across all harnesses, enriched for listing."""
    return [session for harness in AGENTS.values() for session in harness.sessions()]


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


def resume(
    worktree: Path,
    name: str,
    prompt: str | None = None,
    extra: Sequence[str] = (),
    dangerous: bool = False,
    spec: AgentSpec = AgentSpec(),
    context: Path | None = None,
    dry: Dry = Dry(),
) -> None:
    """Reattach to ``spec``'s agent session named ``name`` (see ``Agent.resume``)."""
    dry(
        spec.agent.resume,
        worktree,
        name,
        prompt,
        extra,
        dangerous,
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
