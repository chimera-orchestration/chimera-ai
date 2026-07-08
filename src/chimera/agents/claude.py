"""The claude-code harness: launching, resuming and listing ``claude`` sessions."""

import json
import re
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from loguru import logger

from chimera.agents import Agent, Session

# claude only makes bypass-permissions mode reachable via shift-tab when launched with this;
# availability is fixed at launch, so it must ride on the very command that starts the session.
ALLOW_BYPASS = '--allow-dangerously-skip-permissions'

# the forms that already arrange for bypass mode — don't double up if the caller passed one,
# and refuse both outright when chimera itself is driven by an AI agent (Agent.restricted)
_BYPASS_FLAGS = frozenset({ALLOW_BYPASS, '--dangerously-skip-permissions'})


class Claude(Agent):
    """The claude-code harness (the ``claude`` CLI).

    ``projects`` is where claude keeps its per-cwd transcript folders (default
    ``~/.claude/projects``) — session summaries are read from there; tests point it
    at a scratch tree.
    """

    platform = 'claude'

    restricted = _BYPASS_FLAGS

    def __init__(self, projects: Path | None = None) -> None:
        self.projects = projects

    def start(
        self,
        cwd: Path,
        name: str,
        prompt: str | None = None,
        extra: Sequence[str] = (),
        dangerous: bool = False,
        *,
        model: str | None = None,
        context: Path | None = None,
        exclusive: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run a claude session named ``name``, with cwd set to ``cwd``.

        Runs interactively in the foreground unless ``prompt`` is given, in which case
        it daemonizes (``claude --bg``) to work on the prompt autonomously. ``model``
        rides as ``--model``. ``extra`` is passed straight through to ``claude`` (e.g.
        ``--dangerously-skip-permissions``). ``dangerous`` makes bypass-permissions mode
        reachable (see ``_session_args``).
        """
        args = _session_args(['--name', name], prompt, extra, dangerous, model, context)
        return self._launch(cwd, args, exclusive)

    def resume(
        self,
        cwd: Path,
        name: str,
        prompt: str | None = None,
        extra: Sequence[str] = (),
        dangerous: bool = False,
        *,
        model: str | None = None,
        context: Path | None = None,
        exclusive: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        """Resume the claude session named ``name``, with cwd set to ``cwd``.

        The inverse of :meth:`start`: where that launches with ``--name <name>``, this
        reattaches to the same label with ``--resume <name>``. The cwd is the key —
        claude has no ``--cwd``, so setting it here is what lets a dead session be
        revived in its worktree from anywhere. Interactive foreground by default; with
        ``prompt`` it resumes in the background (``--bg``) to keep working.
        """
        args = _session_args(['--resume', name], prompt, extra, dangerous, model, context)
        return self._launch(cwd, args, exclusive)

    def sessions(self) -> list[Session]:
        """Every verified-live claude session, enriched with a one-line summary.

        The summary is the session's title or last prompt (see :func:`session_summary`),
        read from its transcript under ``projects``.
        """
        return [self._enriched(session) for session in self.live()]

    def reported(self, cwd: Path | None = None) -> list[Session]:
        """What claude's own registry (``claude agents --json``) claims is live.

        One piece of claude-registry knowledge applies here rather than in the shared
        ``live()``: after a session dies, the registry can briefly keep a *degraded*
        entry with pid and status stripped. Such an entry isn't "a session with no pid
        to claim" — it's a remnant, so it's dropped (and logged) at the source.
        """
        scope = ('--cwd', str(cwd)) if cwd is not None else ()
        result = subprocess.run(
            ['claude', 'agents', '--json', *scope],
            capture_output=True,
            text=True,
            check=True,
        )
        claims: list[Session] = []
        for raw in json.loads(result.stdout):
            if not isinstance(raw.get('pid'), int):
                logger.bind(session=raw).warning('agent: session has no pid, treating as stale')
                continue
            claims.append(_parse(raw))
        return claims

    def _enriched(self, session: Session) -> Session:
        if session.cwd == Path('.'):  # registry entry had no cwd — no transcript folder to read
            return session
        summary = session_summary(str(session.cwd), session.name, self.projects)
        return replace(session, summary=summary)

    def _launch(
        self, cwd: Path, args: Sequence[str], exclusive: bool
    ) -> subprocess.CompletedProcess[bytes]:
        """Run ``claude <args>`` in ``cwd``; under ``exclusive``, refuse if one is already live."""
        if not cwd.is_dir():
            raise FileNotFoundError(cwd)
        if exclusive and (running := self.live(cwd)):
            ids = ', '.join(f'{s.id} ({s.status})' for s in running)
            raise RuntimeError(f'an agent is already live in {cwd}: {ids} — attach or stop it')
        return subprocess.run(['claude', *args], cwd=cwd, check=True)


def _parse(raw: dict[str, object]) -> Session:
    """A registry record as a :class:`Session` (summary left for listing enrichment)."""
    # Prefer the full sessionId (the transcript-filename UUID) over `id`, claude's
    # short handle — the short form is that UUID's own leading block anyway.
    id = str(raw.get('sessionId') or raw.get('id') or '?')
    name = str(raw.get('name') or id)
    started = raw.get('startedAt')
    return Session(
        id=id,
        name=name,
        status=str(raw.get('status') or raw.get('state') or '?'),
        cwd=Path(str(raw.get('cwd') or '')),
        summary=None,
        pid=pid if isinstance(pid := raw.get('pid'), int) else None,
        kind=str(kind) if (kind := raw.get('kind')) else None,
        started=datetime.fromtimestamp(started / 1000)
        if isinstance(started, int | float)
        else None,
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


def _session_args(
    lead: list[str],
    prompt: str | None,
    extra: Sequence[str],
    dangerous: bool,
    model: str | None = None,
    context: Path | None = None,
) -> list[str]:
    """The claude argv tail: ``--bg`` when backgrounding, the lead, passthrough, then prompt.

    ``model`` rides as ``--model`` on the lead — unless ``extra`` already carries one, so
    an explicit ``-- --model X`` passthrough always beats the resolved spec. ``context``
    rides as ``--append-system-prompt-file``, injecting the rendered launch context
    before turn 1 with the repo left untouched.

    With ``dangerous`` the session also gets ``--allow-dangerously-skip-permissions`` (unless
    ``extra`` already asks for bypass) so bypass-permissions mode is reachable with shift-tab.
    It's opt-in: enabling bypass *displaces* auto-accept from claude's shift-tab cycle, so the
    everyday default keeps auto-accept and only an explicit request pays that cost. A ``--bg``
    session is an attachable fork, not headless — you cycle after attaching — and the mode's
    availability is decided at *its* launch, so the flag has to ride the background launch too.
    The flag only enables the mode; the autonomous run keeps its resolved mode.
    """
    if model is not None and '--model' not in extra:
        lead = [*lead, '--model', model]
    if context is not None:
        lead = [*lead, '--append-system-prompt-file', str(context)]
    allow = (ALLOW_BYPASS,) if dangerous and not _BYPASS_FLAGS.intersection(extra) else ()
    if prompt is not None:
        return ['--bg', *lead, *extra, *allow, prompt]
    return [*lead, *extra, *allow]
