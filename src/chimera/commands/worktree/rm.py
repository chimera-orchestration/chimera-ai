from datetime import datetime
from pathlib import Path

from giterator import Git

from chimera.commands.agent import live_sessions
from chimera.worktrees import (
    ACTORS,
    AGENT,
    base_ref,
    branch,
    fetch_origin,
    is_dirty,
    is_merged,
    registered_worktrees,
    worktree_path,
)


def remove(
    repo: Path, worktrees_root: Path, goal: str, force: bool = False, fetch: bool = True
) -> list[Path]:
    """Remove the goal's worktrees and branches; refuse on unsaved work unless force.

    Only touches worktrees/branches that actually exist, so re-running — or removing
    a goal that was never fully created — is a safe no-op. Refuses if a claude agent
    is live in the agent worktree, unless force. ``fetch`` (the default) refreshes
    ``origin`` first so a branch merged upstream is recognised as merged. Returns
    removed worktrees.
    """
    git = Git(repo)
    if not force:
        refuse_if_agent_running(worktree_path(worktrees_root, goal, AGENT))
    registered = registered_worktrees(git)
    branches = set(git.branches())
    worktrees = {actor: worktree_path(worktrees_root, goal, actor) for actor in ACTORS}
    if not force:
        if fetch:
            fetch_origin(git)
        _refuse_if_unsafe(git, goal, worktrees, registered, branches)
    removed: list[Path] = []
    for actor, worktree in worktrees.items():
        if worktree.resolve() in registered:
            git('worktree', 'remove', *(('--force',) if force else ()), str(worktree))
            removed.append(worktree)
        if (ref := branch(goal, actor)) in branches:
            # -D not -d: _refuse_if_unsafe is the authority on what's safe to drop (it sees
            # squash/rebase merges that git's ancestry-only -d would wrongly call unmerged).
            git('branch', '-D', ref)
    return removed


def refuse_if_agent_running(agent_worktree: Path) -> None:
    if sessions := live_sessions(agent_worktree):
        described = '\n  '.join(_describe(session) for session in sessions)
        raise RuntimeError(
            f'an agent is live in {agent_worktree}:\n'
            f'  {described}\n'
            f'find its terminal or kill the pid, then re-run'
        )


def _describe(session: dict[str, object]) -> str:
    fields = [f'pid {session.get("pid", "?")}']
    fields += [str(value) for key in ('kind', 'status') if (value := session.get(key))]
    if isinstance(ms := session.get('startedAt'), int | float):
        fields.append(f'since {datetime.fromtimestamp(ms / 1000):%a %H:%M}')
    if name := session.get('name'):
        fields.append(str(name))
    return '  '.join(fields)


def _refuse_if_unsafe(
    git: Git, goal: str, worktrees: dict[str, Path], registered: set[Path], branches: set[str]
) -> None:
    base = base_ref(git)
    problems: list[str] = []
    for actor, worktree in worktrees.items():
        if worktree.resolve() in registered and is_dirty(worktree):
            problems.append(f'{worktree} has uncommitted or untracked changes')
        ref = branch(goal, actor)
        if ref in branches and not (base is not None and is_merged(git, ref, base)):
            problems.append(f'branch {ref} has unmerged commits')
    if problems:
        joined = '\n  '.join(problems)
        raise RuntimeError(f'refusing to clean up (use --force to discard):\n  {joined}')
