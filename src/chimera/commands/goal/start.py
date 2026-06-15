from collections.abc import Sequence
from pathlib import Path

from chimera.commands.agent import agent
from chimera.commands.worktree.add import add
from chimera.worktrees import AGENT, worktree_path


def start(
    repo: Path,
    worktrees_root: Path,
    goal: str,
    name: str,
    prompt: str | None = None,
    frm: str | None = None,
    extra: Sequence[str] = (),
) -> Path:
    """Create the goal's worktrees and branches, then launch its agent.

    Composes ``worktree add`` (the default actor set) with ``agent``: the agent
    runs interactively in the foreground unless ``prompt`` is given, in which case
    it runs in the background. ``extra`` passes straight through to ``claude``.
    Returns the agent worktree.
    """
    add(repo, worktrees_root, goal, frm=frm)
    agent_worktree = worktree_path(worktrees_root, goal, AGENT)
    agent(agent_worktree, name, prompt, extra)
    return agent_worktree
