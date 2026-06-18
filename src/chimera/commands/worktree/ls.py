from pathlib import Path

from chimera.worktrees import worktree_dirs


def ls(worktrees_root: Path) -> list[Path]:
    """The goal worktrees managed under a project's ``worktrees/`` dir."""
    return worktree_dirs(worktrees_root)
