import subprocess
from pathlib import Path


def agent(
    worktree: Path, name: str, prompt: str | None = None
) -> subprocess.CompletedProcess[bytes]:
    """Run a background claude agent named <name>, with cwd set to the worktree.

    `claude --bg` daemonizes itself, so we wait for it to finish printing and
    detaching — otherwise its output races the shell prompt.
    """
    if not worktree.is_dir():
        raise FileNotFoundError(worktree)
    command = ['claude', '--bg', '--name', name]
    if prompt is not None:
        command.append(prompt)
    return subprocess.run(command, cwd=worktree, check=True)
