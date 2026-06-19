from collections.abc import Sequence
from pathlib import Path

from giterator import Git
from loguru import logger

from chimera.commands.agent import agent
from chimera.worktrees import AGENT, HUMAN, branch, ref_shas, registered_worktrees, worktree_path


def adopt(
    repo: Path,
    worktrees_root: Path,
    goal: str,
    name: str,
    prompt: str | None = None,
    extra: Sequence[str] = (),
) -> Path:
    """Adopt an existing branch ``<goal>`` as a goal, then launch its agent.

    Restructures the branch into ``<goal>/human`` and ``<goal>/agent`` — preserving its
    commits as the base — creates the agent worktree, and launches the agent, otherwise
    behaving like ``goal start``. Idempotent: the restructure is skipped once both actor
    branches exist, and the worktree is reused when it is already checked out. Returns the
    agent worktree.

    Because the restructure rewrites refs, the goal's branches and the commits they point at
    are logged before/after the change (see ``agent-docs/logging.md``): the ``before`` snapshot
    is captured prior to touching anything, so the record can restore what the rename moved.
    """
    git = Git(repo)
    before = goal_refs(git, goal)
    restructure(git, goal)
    agent_worktree = ensure_worktree(git, worktrees_root, goal)
    logger.bind(
        goal=goal,
        git={'before': before, 'after': goal_refs(git, goal)},
        worktree=str(agent_worktree),
    ).info('goal adopt: refs')
    agent(agent_worktree, name, prompt, extra)
    return agent_worktree


def goal_refs(git: Git, goal: str) -> dict[str, str]:
    """The goal's branches that currently exist, each mapped to the commit it points at.

    Covers the branch being adopted (``<goal>``) and both actor branches, so the same
    snapshot describes the state before adoption (the bare branch) and after (the pair).
    """
    return ref_shas(git, goal, branch(goal, HUMAN), branch(goal, AGENT))


def restructure(git: Git, goal: str) -> None:
    """Turn an existing branch ``<goal>`` into ``<goal>/human`` and ``<goal>/agent``.

    A no-op once both actor branches exist (the goal was adopted before). Otherwise the
    original branch is *renamed* to the human branch — git can't hold ``refs/heads/<goal>``
    alongside ``refs/heads/<goal>/*``, and the rename atomically dodges that clash while
    carrying any checkout's HEAD along — then the agent branch is split off that same tip.
    """
    branches = set(git.branches())
    human, agent_branch = branch(goal, HUMAN), branch(goal, AGENT)
    if goal in branches:  # not yet adopted — the bare branch still blocks <goal>/*
        git('branch', '-m', goal, human)
    elif human not in branches:
        raise RuntimeError(f'no branch {goal!r} to adopt')
    if agent_branch not in branches:
        git('branch', '--no-track', agent_branch, human)


def ensure_worktree(git: Git, worktrees_root: Path, goal: str) -> Path:
    """Check out ``<goal>/agent`` at ``<goal>@agent``, reusing the worktree if it exists."""
    worktree = worktree_path(worktrees_root, goal, AGENT)
    if worktree.resolve() not in registered_worktrees(git):
        worktrees_root.mkdir(parents=True, exist_ok=True)
        git('worktree', 'add', str(worktree), branch(goal, AGENT))
    return worktree
