from collections.abc import Mapping, Sequence
from pathlib import Path

from chimera.agents.registry import AgentSpec
from chimera.commands.agent import agent
from chimera.dry import Dry
from chimera.git import Git
from chimera.worktrees import AGENT, HUMAN, branch, registered_worktrees, worktree_path


def adopt(
    repo: Path,
    worktrees_root: Path,
    goal: str,
    name: str,
    prompt: str | None = None,
    extra: Sequence[str] = (),
    dangerous: bool = False,
    spec: AgentSpec = AgentSpec(),
    context: Path | None = None,
    env: Mapping[str, str] = {},
    dry: Dry = Dry(),
) -> Path:
    """Adopt an existing branch ``<goal>`` as a goal, then launch its agent.

    Restructures the branch into ``<goal>/human`` and ``<goal>/agent`` — preserving its
    commits as the base — creates the agent worktree, and launches the agent, otherwise
    behaving like ``goal start`` (``env`` is the role stamp, as there).
    ``dangerous`` makes bypass-permissions mode reachable.
    Idempotent: the restructure is skipped once both actor branches exist, and the worktree
    is reused when it is already checked out. Returns the agent worktree.

    Because the restructure rewrites refs, the goal's branches and the commits they point at
    are logged before/after the change (see ``agent-docs/logging.md``): the ``before`` snapshot
    is captured prior to touching anything, so the record can restore what the rename moved.
    """
    git = Git(repo)
    # the snapshot covers the branch being adopted (``<goal>``) and both actor branches, so the
    # same refs describe the state before adoption (the bare branch) and after (the pair);
    # ``always`` because the line lands the worktree too — the recovery record even on a re-run
    agent_worktree = worktree_path(worktrees_root, goal, AGENT)
    with git.ref_log(
        'goal adopt: refs', goal, branch(goal, HUMAN), branch(goal, AGENT), always=True, goal=goal
    ) as refs:
        dry(restructure, git, goal)
        dry(ensure_worktree, git, worktrees_root, goal)
        refs.bind(worktree=str(agent_worktree))
    agent(agent_worktree, name, prompt, extra, dangerous, spec, context, env, dry)
    return agent_worktree


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
