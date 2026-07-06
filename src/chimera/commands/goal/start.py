from collections.abc import Sequence
from pathlib import Path

from chimera.agents.registry import AgentSpec
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
    fetch: bool = True,
    dangerous: bool = False,
    spec: AgentSpec = AgentSpec(),
) -> Path:
    """Create the goal's worktrees and branches, then launch its agent.

    Composes ``worktree add`` (the default actor set) with ``agent``: the agent
    runs interactively in the foreground unless ``prompt`` is given, in which case
    it runs in the background. ``extra`` passes straight through to the harness.
    ``fetch`` (the default) refreshes ``origin`` before choosing the base. ``dangerous``
    makes bypass-permissions mode reachable. ``spec`` picks the harness and model.
    Returns the agent worktree.
    """
    add(repo, worktrees_root, goal=goal, frm=frm, fetch=fetch)
    agent_worktree = worktree_path(worktrees_root, goal, AGENT)
    agent(agent_worktree, name, prompt, extra, dangerous, spec)
    return agent_worktree
