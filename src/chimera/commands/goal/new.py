from pathlib import Path

from giterator import Git, GitError

_ROLES = ('human', 'agent')


def new(repo: Path, worktrees_root: Path, goal: str) -> Path:
    """Create the goal dir with human and agent worktrees on <goal>/human and <goal>/agent."""
    git = Git(repo)
    _require_commit(git, repo)
    goal_dir = worktrees_root / goal
    goal_dir.mkdir(parents=True, exist_ok=True)
    for role in _ROLES:
        git('worktree', 'add', '-b', f'{goal}/{role}', str(goal_dir / role))
    return goal_dir


def _require_commit(git: Git, repo: Path) -> None:
    try:
        git('rev-parse', '--verify', '--quiet', 'HEAD')
    except GitError:
        status = git('status')
        raise RuntimeError(
            f'{repo} has no commits to branch from — commit first:\n\n{status}'
        ) from None
