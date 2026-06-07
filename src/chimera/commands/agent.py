import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Agent:
    """A live agent: its name, status and most recent prompt (a one-line summary)."""

    name: str
    status: str
    summary: str | None


def agents(projects: Path | None = None) -> list[Agent]:
    """Every live agent across all projects, each enriched with a one-line summary.

    The summary is the agent's last prompt, read from its session transcript under
    ``projects`` (default ``~/.claude/projects``); ``None`` when none can be found.
    """
    return [
        Agent(
            name=str(session.get('name') or session['sessionId']),
            status=str(session['status']),
            summary=last_prompt(str(session['sessionId']), projects),
        )
        for session in all_sessions()
    ]


def last_prompt(session_id: str, projects: Path | None = None) -> str | None:
    """The session's most recent prompt, collapsed to a single line, or ``None``."""
    projects = projects if projects is not None else Path.home() / '.claude' / 'projects'
    for transcript in projects.glob(f'*/{session_id}.jsonl'):
        for line in reversed(transcript.read_text().splitlines()):
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get('type') == 'last-prompt':
                return ' '.join(str(record['lastPrompt']).split())
    return None


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
