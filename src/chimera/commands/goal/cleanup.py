from pathlib import Path

from giterator import Git, GitError

from chimera.commands.agent import live_sessions
from chimera.commands.goal import ROLES


def cleanup(repo: Path, worktrees_root: Path, goal: str, force: bool = False) -> list[Path]:
    """Remove the goal's worktrees and branches; refuse on unsaved work unless force.

    Always aborts if a claude agent is live in the agent worktree, even with force.
    """
    git = Git(repo)
    worktrees = [worktrees_root / f'{goal}-{role}' for role in ROLES]
    _refuse_if_agent_running(worktrees_root / f'{goal}-agent')
    if not force:
        _refuse_if_unsafe(git, goal, worktrees)
    for role, worktree in zip(ROLES, worktrees):
        git('worktree', 'remove', *(('--force',) if force else ()), str(worktree))
        git('branch', '-D' if force else '-d', f'{goal}/{role}')
    return worktrees


def _refuse_if_agent_running(agent_worktree: Path) -> None:
    if live_sessions(agent_worktree):
        raise RuntimeError(f'an agent is live in {agent_worktree}; stop it before cleaning up')


def _refuse_if_unsafe(git: Git, goal: str, worktrees: list[Path]) -> None:
    problems: list[str] = []
    for role, worktree in zip(ROLES, worktrees):
        if worktree.is_dir() and Git(worktree)('status', '--porcelain').strip():
            problems.append(f'{worktree} has uncommitted or untracked changes')
        if not _merged(git, f'{goal}/{role}'):
            problems.append(f'branch {goal}/{role} has unmerged commits')
    if problems:
        joined = '\n  '.join(problems)
        raise RuntimeError(f'refusing to clean up (use --force to discard):\n  {joined}')


def _merged(git: Git, branch: str) -> bool:
    try:
        git('merge-base', '--is-ancestor', branch, 'HEAD')
        return True
    except GitError:
        return False
