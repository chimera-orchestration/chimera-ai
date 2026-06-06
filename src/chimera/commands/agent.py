import json
import subprocess
from pathlib import Path


def agent(
    worktree: Path, name: str, prompt: str | None = None
) -> subprocess.CompletedProcess[bytes]:
    """Run a claude agent named <name>, with cwd set to the worktree.

    Runs interactively in the foreground unless ``prompt`` is given, in which case
    it daemonizes (`claude --bg`) to work on the prompt autonomously. Refuses if a
    claude session is already live in the worktree.
    """
    if not worktree.is_dir():
        raise FileNotFoundError(worktree)
    if running := live_sessions(worktree):
        ids = ', '.join(f'{s["sessionId"]} ({s["status"]})' for s in running)
        raise RuntimeError(f'an agent is already live in {worktree}: {ids} — attach or stop it')
    command = ['claude']
    if prompt is not None:
        command.append('--bg')
    command += ['--name', name]
    if prompt is not None:
        command.append(prompt)
    return subprocess.run(command, cwd=worktree, check=True)


def live_sessions(worktree: Path) -> list[dict[str, object]]:
    """Claude sessions currently live under the worktree."""
    return _sessions('--cwd', str(worktree))


def all_sessions() -> list[dict[str, object]]:
    """Claude sessions currently live anywhere (across all projects)."""
    return _sessions()


def _sessions(*scope: str) -> list[dict[str, object]]:
    result = subprocess.run(
        ['claude', 'agents', '--json', *scope],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)
