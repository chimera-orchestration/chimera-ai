from pathlib import Path

from giterator import Git

_ROLES = ('human', 'agent')


def new(repo: Path, worktrees_root: Path, goal: str) -> Path:
    """Create the goal dir with human and agent worktrees on <goal>/human and <goal>/agent."""
    goal_dir = worktrees_root / goal
    goal_dir.mkdir(parents=True, exist_ok=True)
    git = Git(repo)
    for role in _ROLES:
        git('worktree', 'add', '-b', f'{goal}/{role}', str(goal_dir / role))
    return goal_dir
