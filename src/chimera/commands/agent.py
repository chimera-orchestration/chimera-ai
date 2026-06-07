import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Agent:
    """A live agent: its name, status, working directory and most recent prompt."""

    name: str
    status: str
    cwd: Path
    summary: str | None

    @property
    def detail(self) -> str:
        """One-line description: the last prompt, falling back to the cwd."""
        if self.summary:
            return self.summary
        home = str(Path.home())
        cwd = str(self.cwd)
        return '~' + cwd[len(home) :] if cwd.startswith(home) else cwd


def agents(projects: Path | None = None) -> list[Agent]:
    """Every live agent across all projects, each enriched with a one-line summary.

    The summary is the agent's last prompt, read from its session transcript under
    ``projects`` (default ``~/.claude/projects``); ``None`` when none can be found.
    """
    return [
        Agent(
            name=str(session.get('name') or session['sessionId']),
            status=str(session['status']),
            cwd=Path(str(session['cwd'])),
            summary=last_prompt(str(session['cwd']), projects),
        )
        for session in all_sessions()
    ]


def last_prompt(cwd: str, projects: Path | None = None) -> str | None:
    """The most recent prompt of the live session in ``cwd``, collapsed to one line.

    Claude stores each session's transcript under a per-cwd folder of ``projects``
    (``~/.claude/projects`` by default). The live session's reported id rarely names
    its transcript (background agents resume under fresh ids), so we read the newest
    transcript in that folder — the one currently being appended to — and return its
    ``last-prompt`` record. ``None`` when the folder or record is absent.
    """
    projects = projects if projects is not None else Path.home() / '.claude' / 'projects'
    folder = projects / re.sub(r'[^a-zA-Z0-9]', '-', cwd)
    transcripts = sorted(folder.glob('*.jsonl'), key=lambda p: p.stat().st_mtime, reverse=True)
    for line in reversed(transcripts[0].read_text().splitlines()) if transcripts else ():
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
