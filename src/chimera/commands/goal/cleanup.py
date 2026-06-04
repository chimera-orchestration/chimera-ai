from pathlib import Path

from giterator import Git, GitError

from chimera.commands.agent import live_sessions
from chimera.commands.goal import AGENT, ROLES


def cleanup(repo: Path, worktrees_root: Path, goal: str, force: bool = False) -> list[Path]:
    """Remove the goal's worktrees and branches; refuse on unsaved work unless force.

    Only touches worktrees/branches that actually exist, so re-running — or cleaning
    a goal that was never fully created — is a safe no-op. Always aborts if a claude
    agent is live in the agent worktree, even with force.
    """
    git = Git(repo)
    _refuse_if_agent_running(worktrees_root / f'{goal}-{AGENT}')
    registered = _registered_worktrees(git)
    branches = set(git.branches())
    worktrees = [worktrees_root / f'{goal}-{role}' for role in ROLES]
    if not force:
        _refuse_if_unsafe(git, goal, worktrees, registered, branches)
    removed: list[Path] = []
    for role, worktree in zip(ROLES, worktrees):
        if worktree.resolve() in registered:
            git('worktree', 'remove', *(('--force',) if force else ()), str(worktree))
            removed.append(worktree)
        branch = f'{goal}/{role}'
        if branch in branches:
            git('branch', '-D' if force else '-d', branch)
    return removed


def _registered_worktrees(git: Git) -> set[Path]:
    out = git('worktree', 'list', '--porcelain')
    return {
        Path(line.removeprefix('worktree ')).resolve()
        for line in out.splitlines()
        if line.startswith('worktree ')
    }


def _refuse_if_agent_running(agent_worktree: Path) -> None:
    if live_sessions(agent_worktree):
        raise RuntimeError(f'an agent is live in {agent_worktree}; stop it before cleaning up')


def _refuse_if_unsafe(
    git: Git, goal: str, worktrees: list[Path], registered: set[Path], branches: set[str]
) -> None:
    problems: list[str] = []
    for role, worktree in zip(ROLES, worktrees):
        if worktree.resolve() in registered and Git(worktree)('status', '--porcelain').strip():
            problems.append(f'{worktree} has uncommitted or untracked changes')
        branch = f'{goal}/{role}'
        if branch in branches and not _merged(git, branch):
            problems.append(f'branch {branch} has unmerged commits')
    if problems:
        joined = '\n  '.join(problems)
        raise RuntimeError(f'refusing to clean up (use --force to discard):\n  {joined}')


def _merged(git: Git, branch: str) -> bool:
    try:
        git('merge-base', '--is-ancestor', branch, 'HEAD')
        return True
    except GitError:
        return False
