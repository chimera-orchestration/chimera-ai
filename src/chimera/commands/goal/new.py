from pathlib import Path

from giterator import Git, GitError

from chimera.commands.goal import ROLES


def new(repo: Path, worktrees_root: Path, goal: str) -> list[Path]:
    """Create <goal>-human and <goal>-agent worktrees on branches <goal>/human and <goal>/agent."""
    git = Git(repo)
    _require_commit(git, repo)
    worktrees_root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for role in ROLES:
        worktree = worktrees_root / f'{goal}-{role}'
        git('worktree', 'add', '-b', f'{goal}/{role}', str(worktree))
        created.append(worktree)
    return created


def _require_commit(git: Git, repo: Path) -> None:
    try:
        git('rev-parse', '--verify', '--quiet', 'HEAD')
    except GitError:
        status = git('status')
        raise RuntimeError(
            f'{repo} has no commits to branch from — commit first:\n\n{status}'
        ) from None
