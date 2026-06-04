import subprocess
from pathlib import Path


def agent(worktree: Path, name: str) -> subprocess.Popen[bytes]:
    """Launch a background claude agent named <name>, with cwd set to the worktree."""
    if not worktree.is_dir():
        raise FileNotFoundError(worktree)
    return subprocess.Popen(['claude', '--bg', '--name', name], cwd=worktree)
