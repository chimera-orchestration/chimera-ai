import json
import subprocess
from pathlib import Path


def agent(
    worktree: Path, name: str, prompt: str | None = None
) -> subprocess.CompletedProcess[bytes]:
    """Run a background claude agent named <name>, with cwd set to the worktree.

    Refuses if a claude session is already live in the worktree. `claude --bg`
    daemonizes itself, so we wait for it to finish printing and detaching.
    """
    if not worktree.is_dir():
        raise FileNotFoundError(worktree)
    running = live_sessions(worktree)
    if running:
        ids = ', '.join(f'{s["sessionId"]} ({s["status"]})' for s in running)
        raise RuntimeError(f'an agent is already live in {worktree}: {ids} — attach or stop it')
    command = ['claude', '--bg', '--name', name]
    if prompt is not None:
        command.append(prompt)
    return subprocess.run(command, cwd=worktree, check=True)


def live_sessions(worktree: Path) -> list[dict[str, object]]:
    """Claude sessions currently live under the worktree (via `claude agents --json`)."""
    result = subprocess.run(
        ['claude', 'agents', '--json', '--cwd', str(worktree)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)
