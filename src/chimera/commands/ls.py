from dataclasses import dataclass

from chimera.commands.agent import Agent, in_goal, scoped, under
from chimera.context import Scope, iter_projects
from chimera.worktrees import goals


@dataclass(frozen=True)
class GoalBoard:
    """A goal in flight and the agents working it."""

    name: str
    agents: list[Agent]


@dataclass(frozen=True)
class ProjectBoard:
    """A project, its goals, and any in-scope agents not tied to a goal worktree."""

    name: str
    goals: list[GoalBoard]
    loose: list[Agent]


@dataclass(frozen=True)
class Board:
    """The whole picture within a scope: projects, their goals, and stray agents."""

    workspace: str
    projects: list[ProjectBoard]
    loose: list[Agent]


def board(scope: Scope, listing: list[Agent]) -> Board:
    """Partition the in-scope agents into a project → goal tree, surfacing strays as ``loose``.

    Every agent in scope lands exactly once: under its goal when its cwd is in a goal
    worktree, else as project ``loose`` (e.g. a session in ``repo/``), else as board
    ``loose`` (under the workspace but no project) — a running agent is never dropped.
    """
    universe = scoped(listing, scope)
    projects = [scope.project] if scope.project is not None else iter_projects(scope.workspace)
    boards: list[ProjectBoard] = []
    placed: set[Agent] = set()
    for p in projects:
        names = sorted(goals(p.worktrees))
        if scope.goal is not None:
            names = [g for g in names if g == scope.goal]
        goal_boards = [
            GoalBoard(g, [a for a in universe if in_goal(a.cwd, p.worktrees, g)]) for g in names
        ]
        in_goals = {a for gb in goal_boards for a in gb.agents}
        in_project = [a for a in universe if under(a.cwd, p.dir)]
        placed.update(in_project)
        boards.append(
            ProjectBoard(p.name, goal_boards, [a for a in in_project if a not in in_goals])
        )
    return Board(scope.workspace.name, boards, [a for a in universe if a not in placed])
