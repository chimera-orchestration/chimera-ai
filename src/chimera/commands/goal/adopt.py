from collections.abc import Sequence
from pathlib import Path

from chimera.agents.registry import AgentSpec
from chimera.commands.agent import agent
from chimera.config import UserError
from chimera.dry import Dry
from chimera.git import Git
from chimera.worktrees import (
    AGENT,
    HUMAN,
    branch,
    registered_worktrees,
    require_valid_goal,
    worktree_path,
)


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
    dry: Dry = Dry(),
) -> Path:
    """Adopt an existing branch ``<goal>`` as a goal, then launch its agent.

    Restructures the branch into ``<goal>/human`` and ``<goal>/agent`` — preserving its
    commits as the base — creates the agent worktree, and launches the agent, otherwise
    behaving like ``goal start`` (``env`` is the role stamp, as there).
    ``dangerous`` makes bypass-permissions mode reachable.
    Idempotent: the restructure is skipped once both actor branches exist, and the worktree
    is reused when it is already checked out. Returns the agent worktree.

    Under ``dry`` every check still runs — the branch discovery and its refusal included —
    and only the mutations are skipped, so a preview refuses exactly where a real run would.

    Because the restructure rewrites refs, the goal's branches and the commits they point at
    are logged before/after the change (see ``agent-docs/logging.md``): the ``before`` snapshot
    is captured prior to touching anything, so the record can restore what the rename moved.
    """
    # the adopted branch *becomes* the goal name verbatim, so it must fit the goal grammar —
    # a '/'-nested branch can't be adopted (and, before the Dry guard, --dry refuses it too)
    require_valid_goal(goal)
    git = Git(repo)
    # the snapshot covers the branch being adopted (``<goal>``) and both actor branches, so the
    # same refs describe the state before adoption (the bare branch) and after (the pair);
    # ``always`` because the line lands the worktree too — the recovery record even on a re-run
    agent_worktree = worktree_path(worktrees_root, goal, AGENT)
    with git.ref_log(
        'goal adopt: refs', goal, branch(goal, HUMAN), branch(goal, AGENT), always=True, goal=goal
    ) as refs:
        restructure(git, goal, dry)
        ensure_worktree(git, worktrees_root, goal, dry)
        refs.bind(worktree=str(agent_worktree))
    agent(agent_worktree, name, prompt, extra, dangerous, spec, context, dry)
    return agent_worktree


def restructure(git: Git, goal: str, dry: Dry = Dry()) -> None:
    """Turn an existing branch ``<goal>`` into ``<goal>/human`` and ``<goal>/agent``.

    A no-op once both actor branches exist (the goal was adopted before). Otherwise the
    original branch is *renamed* to the human branch — git can't hold ``refs/heads/<goal>``
    alongside ``refs/heads/<goal>/*``, and the rename atomically dodges that clash while
    carrying any checkout's HEAD along — then the agent branch is split off that same tip.

    A goal left with only ``<goal>/agent`` — its session gone, so the agent needs relaunching
    with nothing to resume — is adopted too: the human branch is materialised at the agent's
    tip, as ``goal sync`` would create it. The *local* tip, deliberately: adopt never fetches,
    and a remote counterpart that has moved on is ``goal sync``'s to integrate.

    Discovery and the refusal run whatever ``dry`` says; only the ref writes go through it.
    """
    branches = set(git.branches())
    human, agent_branch = branch(goal, HUMAN), branch(goal, AGENT)
    if goal in branches:  # not yet adopted — the bare branch still blocks <goal>/*
        dry(git, 'branch', '-m', goal, human)
    elif human not in branches:
        if agent_branch not in branches:
            raise UserError(f'no branch {goal!r} to adopt')
        dry(git, 'branch', '--no-track', human, agent_branch)
    if agent_branch not in branches:
        dry(git, 'branch', '--no-track', agent_branch, human)


def ensure_worktree(git: Git, worktrees_root: Path, goal: str, dry: Dry = Dry()) -> Path:
    """Check out ``<goal>/agent`` at ``<goal>@agent``, reusing the worktree if it exists."""
    worktree = worktree_path(worktrees_root, goal, AGENT)
    if worktree.resolve() not in registered_worktrees(git):
        dry(worktrees_root.mkdir, parents=True, exist_ok=True)
        dry(git, 'worktree', 'add', str(worktree), branch(goal, AGENT))
    return worktree
