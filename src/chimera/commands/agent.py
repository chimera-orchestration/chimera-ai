import subprocess
from pathlib import Path


def agent(worktree: Path, name: str) -> subprocess.CompletedProcess[bytes]:
    """Run a background claude agent named <name>, with cwd set to the worktree.

    `claude --bg` daemonizes itself, so we wait for it to finish printing and
    detaching — otherwise its output races the shell prompt.
    """
    if not worktree.is_dir():
        raise FileNotFoundError(worktree)
    return subprocess.run(['claude', '--bg', '--name', name], cwd=worktree, check=True)
