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
        """One-line description: the session title or last prompt, else the cwd."""
        if self.summary:
            return self.summary
        home = str(Path.home())
        cwd = str(self.cwd)
        return '~' + cwd[len(home) :] if cwd.startswith(home) else cwd


def agents(projects: Path | None = None) -> list[Agent]:
    """Every live agent across all projects, each enriched with a one-line summary.

    The summary is the agent's session title or last prompt (see ``session_summary``),
    read from its transcript under ``projects`` (default ``~/.claude/projects``).
    """
    return [_describe(session, projects) for session in all_sessions()]


def _describe(session: dict[str, object], projects: Path | None) -> Agent:
    name = str(session.get('name') or session.get('sessionId') or '?')
    cwd = str(session.get('cwd') or '')
    return Agent(
        name=name,
        status=str(session.get('status') or session.get('state') or '?'),
        cwd=Path(cwd),
        summary=session_summary(cwd, name, projects) if cwd else None,
    )


# Transcript metadata records Claude resolves a session label from, mapping each
# record `type` to the field carrying its value — highest precedence first.
TITLES = {'custom-title': 'customTitle', 'ai-title': 'aiTitle', 'last-prompt': 'lastPrompt'}


def session_summary(cwd: str, name: str, projects: Path | None = None) -> str | None:
    """A one-line summary of the live session in ``cwd``: its title or last prompt.

    Claude stores each session's transcript under a per-cwd folder of ``projects``
    (``~/.claude/projects`` by default). The live session's reported id rarely names
    its transcript (background agents resume under fresh ids), so we read the newest
    transcript in that folder — the one currently being appended to — and mirror
    Claude's own precedence: a user-set title, then an AI-generated topic, then the
    last prompt. Anything equal to ``name`` is skipped — Claude persists ``--name``
    as a title, so it would merely echo what we already show. ``None`` when nothing
    distinct remains.
    """
    projects = projects if projects is not None else Path.home() / '.claude' / 'projects'
    folder = projects / re.sub(r'[^a-zA-Z0-9]', '-', cwd)
    transcripts = sorted(folder.glob('*.jsonl'), key=lambda p: p.stat().st_mtime, reverse=True)
    latest: dict[str, str] = {}
    for line in reversed(transcripts[0].read_text().splitlines()) if transcripts else ():
        if not line.strip():
            continue
        record = json.loads(line)
        field = TITLES.get(str(record.get('type')))
        value = record.get(field) if field else None  # a typed record may omit its value
        if field and value and field not in latest:  # reversed, so first seen is the file's latest
            latest[field] = ' '.join(str(value).split())
            if len(latest) == len(TITLES):
                break
    return next((latest[f] for f in TITLES.values() if f in latest and latest[f] != name), None)


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
