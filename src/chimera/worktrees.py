from pathlib import Path

from giterator import Git, GitError


def registered_worktrees(git: Git) -> set[Path]:
    """The worktree paths git knows about for repo, resolved."""
    out = git('worktree', 'list', '--porcelain')
    return {
        Path(line.removeprefix('worktree ')).resolve()
        for line in out.splitlines()
        if line.startswith('worktree ')
    }


def is_merged(git: Git, ref: str) -> bool:
    """Whether ref is an ancestor of the repo's current HEAD (nothing unmerged)."""
    try:
        git('merge-base', '--is-ancestor', ref, 'HEAD')
        return True
    except GitError:
        return False


def is_dirty(worktree: Path) -> bool:
    """Whether the worktree has uncommitted or untracked changes."""
    return bool(Git(worktree)('status', '--porcelain').strip())
