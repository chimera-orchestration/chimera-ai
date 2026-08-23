"""The claude-code harness: launching, resuming and listing ``claude`` sessions."""

import json
import os
import re
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from functools import cache
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from loguru import logger

from chimera.agents import Agent, Session
from chimera.config import UserError

# claude only makes bypass-permissions mode reachable via shift-tab when launched with this;
# availability is fixed at launch, so it must ride on the very command that starts the session.
ALLOW_BYPASS = '--allow-dangerously-skip-permissions'

# the forms that already arrange for bypass mode — don't double up if the caller passed one,
# and refuse both outright when chimera itself is driven by an AI agent (Agent.restricted)
_BYPASS_FLAGS = frozenset({ALLOW_BYPASS, '--dangerously-skip-permissions'})

# Agent.run's readonly capability hint in claude's own permission grammar: file inspection
# plus git archaeology, nothing else — the wall blocks Write/Edit and general Bash outright.
# Not watertight, though: claude's Bash allowlist is prefix-matching, so these curated git
# commands admit git's own flags, including ones that write (e.g. `git log --output=<path>`)
# — an accepted, bounded residual; the ephemeral worktree, the sweep and the caller's own
# audit of the report are the containment. Deliberately conservative otherwise: a tool not
# listed is simply unavailable to the run.
READONLY_TOOLS = (
    'Read',
    'Grep',
    'Glob',
    'Bash(git log:*)',
    'Bash(git show:*)',
    'Bash(git grep:*)',
    'Bash(git diff:*)',
    'Bash(git blame:*)',
    'Bash(git status:*)',
)


# Daemon job states that mean the job is over — a pid-less entry in one of these is a
# corpse however complete its state.json looks (a done job still carries respawnFlags).
ENDED_STATES = frozenset({'stopped', 'done', 'failed'})


def job_parked(jobs: Path, job_id: str) -> bool:
    """Whether daemon job ``job_id`` is parked — worker reaped but revivable.

    After long idleness claude's daemon parks a ``--bg`` job: the worker process is
    reaped (so the registry entry loses its pid, indistinguishable from a corpse
    there) but ``jobs/<job_id>/state.json`` keeps the ``respawnFlags`` that ``claude
    attach`` relaunches the *same* session from (verified against binary 2.1.239).
    Parked is exactly: that file present and parseable, ``respawnFlags`` non-empty,
    and a ``state`` outside :data:`ENDED_STATES`. Anything else — dir swept, file
    unreadable, no respawn recipe, job over — is dead.
    """
    try:
        state = json.loads((jobs / job_id / 'state.json').read_text())
    except (OSError, ValueError):
        return False
    return bool(state.get('respawnFlags')) and state.get('state') not in ENDED_STATES


class Claude(Agent):
    """The claude-code harness (the ``claude`` CLI).

    ``projects`` is where claude keeps its per-cwd transcript folders (default
    ``~/.claude/projects``) — session summaries are read from there; ``jobs`` is the
    daemon's per-job state (default ``~/.claude/jobs``) — parked-vs-dead classification
    reads it (see :func:`job_parked`). Tests point both at scratch trees.
    """

    platform = 'claude'

    restricted = _BYPASS_FLAGS

    def __init__(self, projects: Path | None = None, jobs: Path | None = None) -> None:
        self.projects = projects
        self.jobs = jobs if jobs is not None else Path.home() / '.claude' / 'jobs'

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
        env: Mapping[str, str] = {},
        exclusive: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run a claude session named ``name``, with cwd set to ``cwd``.

        Runs interactively in the foreground unless ``prompt`` is given, in which case
        it daemonizes (``claude --bg``) to work on the prompt autonomously. ``model``
        rides as ``--model``. ``extra`` is passed straight through to ``claude`` (e.g.
        ``--dangerously-skip-permissions``). ``dangerous`` makes bypass-permissions mode
        reachable (see ``_session_args``). ``env`` is overlaid on the parent environment
        (see ``Agent``).
        """
        args = _session_args(['--name', name], prompt, extra, dangerous, model, context)
        return self._launch(cwd, args, exclusive, env)

    def resume(
        self,
        cwd: Path,
        name: str,
        prompt: str | None = None,
        extra: Sequence[str] = (),
        dangerous: bool = False,
        *,
        id: str | None = None,
        model: str | None = None,
        context: Path | None = None,
        env: Mapping[str, str] = {},
        exclusive: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        """Resume a claude session, with cwd set to ``cwd``.

        With ``id`` (the session's full UUID — ``--resume``'s documented argument) the
        session is resumed by identity and ``--name`` re-asserts the canonical label,
        exactly as :meth:`start` set it — so a rename in claude's own UI neither orphans
        the session nor survives the resume. Without one, ``--resume <name>`` leans on
        claude's name-to-session DWIM, the pre-archive behaviour. The cwd is the key —
        claude has no ``--cwd``, so setting it here is what lets a dead session be
        revived in its worktree from anywhere. Interactive foreground by default; with
        ``prompt`` it resumes in the background (``--bg``) to keep working.
        """
        lead = ['--resume', id, '--name', name] if id is not None else ['--resume', name]
        args = _session_args(lead, prompt, extra, dangerous, model, context)
        return self._launch(cwd, args, exclusive, env)

    def run(
        self,
        cwd: Path,
        name: str,
        prompt: str,
        extra: Sequence[str] = (),
        *,
        model: str | None = None,
        context: Path | None = None,
        env: Mapping[str, str] = {},
        readonly: bool = True,
        timeout: float | None = None,
    ) -> str:
        """Run a one-shot headless claude session in ``cwd``; return its result text.

        ``claude -p --output-format json`` with the same model/context lead as a live
        session (``--append-system-prompt-file`` works in print mode too — verified
        against the live CLI). ``readonly`` maps to an ``--allowedTools`` wall of
        :data:`READONLY_TOOLS`. The JSON envelope's ``result`` is returned, its
        session id / cost / duration landing on an ``errand: run`` log line — the
        pointer back to the run's transcript. An envelope that doesn't parse degrades
        to raw stdout (logged) rather than failing a run that already happened; a
        non-zero exit raises — its captured stderr landing on an ``errand: run
        failed`` ERROR line first, since ``CalledProcessError``'s own text omits it —
        as does an exceeded ``timeout``.
        """
        try:
            result = subprocess.run(
                ['claude', *_print_args(extra, model, context, readonly), *extra, prompt],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=True,
                timeout=timeout,
                env={**os.environ, **env} if env else None,
            )
        except subprocess.CalledProcessError as error:
            logger.bind(
                session=name,
                cwd=str(cwd),
                returncode=error.returncode,
                stderr=_tail(error.stderr),
            ).error('errand: run failed')
            raise
        try:
            envelope = json.loads(result.stdout)
            text = str(envelope['result'])
        except (json.JSONDecodeError, TypeError, KeyError):
            logger.bind(session=name).warning('errand: run envelope did not parse, raw stdout')
            return result.stdout
        logger.bind(
            session=name,
            session_id=envelope.get('session_id'),
            cost_usd=envelope.get('total_cost_usd'),
            duration_ms=envelope.get('duration_ms'),
        ).info('errand: run')
        return text

    def sessions(self) -> list[Session]:
        """Every checked claude session, enriched with a one-line summary.

        Checked, not merely live: stale entries ride along marked, never dropped.
        The summary is the session's title or last prompt (see :func:`session_summary`),
        read from its transcript under ``projects``.
        """
        return [self._enriched(session) for session in self.checked()]

    def reported(self, cwd: Path | None = None) -> list[Session]:
        """What claude's own registry (``claude agents --json``) claims is live.

        Two pieces of claude-registry knowledge apply here rather than in the shared
        ``checked()``. A pid-less entry whose daemon job is still revivable (see
        :func:`job_parked`) is *parked*, not dead — marked ``parked`` with status
        ``parked`` (the registry's own claim, e.g. ``blocked``, describes a worker
        that no longer exists). Otherwise: after a session dies, the registry can
        briefly keep a *degraded* entry with pid and status stripped — a remnant, so
        it's marked stale (and logged) at the source.

        A machine with no ``claude`` binary at all answers with no sessions — nothing
        the harness runs can be live there — so every liveness consumer (worktree rm,
        the launch guards, the listers) keeps working; a present-but-failing ``claude``
        still raises. Logged (once — see :func:`_warn_missing_binary`), never silent.
        """
        scope = ('--cwd', str(cwd)) if cwd is not None else ()
        try:
            result = subprocess.run(
                ['claude', 'agents', '--json', *scope],
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError:
            _warn_missing_binary()
            return []
        claims: list[Session] = []
        for raw in json.loads(result.stdout):
            session = _parse(raw)
            if session.pid is None:
                # the daemon job id is claude's short handle, not the session UUID —
                # though the two agree today (`stop` already leans on that)
                if job_parked(self.jobs, str(raw.get('id') or session.short)):
                    logger.bind(session=raw).info('agent: session is parked (ch wake revives it)')
                    session = replace(session, status='parked', parked=True)
                else:
                    logger.bind(session=raw).warning('agent: session has no pid, treating as stale')
                    session = replace(
                        session, stale='no pid in the registry entry (degraded remnant)'
                    )
            claims.append(session)
        return claims

    def stop(self, session: Session, timeout: float = 10.0) -> None:
        """Stop ``session`` for good.

        A background (``--bg``) session runs under claude's own supervisor, which
        respawns a bare SIGTERM as a crash — a fresh pid picks up the same job within
        seconds, so the session is never actually stopped, only briefly interrupted,
        and the ``claude agents`` bridge is left with a row pointing at a pid that's
        gone (clicking it reports "session ended", but the job itself keeps running).
        Verified live against ``claude`` 2.1.212: a SIGTERM'd ``--bg`` job respawns
        under a new pid within ~5s, while ``claude stop <id>`` cleanly kills both the
        worker and its supervisor and marks the job ``stopped`` (resumable later via
        ``claude attach``). So a background session is stopped through claude's own
        ``stop`` subcommand — keyed by :attr:`Session.short`, the job id ``claude
        stop`` expects (the full ``sessionId`` UUID is *not* accepted). An interactive
        session has no such supervisor and stops cleanly on a plain SIGTERM, so it
        keeps the base behaviour. A *parked* session has no pid to signal at all, but
        ``claude stop`` acts on the daemon's job record, not the worker — so it ends a
        parked job the same way (and only ever parks ``--bg`` jobs anyway).
        """
        if session.kind != 'background' and not session.parked:
            super().stop(session, timeout)
            return
        result = subprocess.run(['claude', 'stop', session.short], capture_output=True, text=True)
        if result.returncode != 0:
            raise UserError(
                f'{session.name} (job {session.short}): `claude stop` failed: '
                f'{result.stderr.strip() or result.stdout.strip()}'
            )
        logger.bind(session=session.name, id=session.short).info('agent stop')

    def wake(self, session: Session, timeout: float = 30.0) -> Session:
        """Respawn the parked ``session``'s worker, keeping its identity — verified, never assumed.

        ``claude attach <job>`` is the one verb that revives the SAME session (and any
        remote bridge riding it): the daemon relaunches the worker from the job's
        ``respawnFlags``. A ``--resume`` would mint a fresh session instead — the
        orphaning this verb exists to avoid. attach needs a tty, so the throwaway
        client runs under ``script(1)`` (BSD spelling — the darwin-verified recipe) in
        its own process group, stdin held open (EOF reads as detach, possibly before
        the respawn lands). The worker is daemon-owned and survives the client, which
        is dismissed once the respawn is verified: the job back in the registry with a
        pid, polled for up to ``timeout`` seconds — the returned session. No pid by
        the deadline raises, naming the by-hand attach.
        """
        job = session.short
        client = subprocess.Popen(
            ['script', '-q', '/dev/null', 'claude', 'attach', job],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                time.sleep(1.0)
                for candidate in self.reported(session.cwd):
                    if candidate.pid is not None and candidate.short == job:
                        logger.bind(session=session.name, id=job, pid=candidate.pid).info(
                            'agent wake'
                        )
                        return candidate
            raise UserError(
                f'{session.name} (job {job}) did not respawn within {timeout:g}s — '
                f'try `claude attach {job}` in a terminal'
            )
        finally:
            _dismiss(client)

    def _enriched(self, session: Session) -> Session:
        if session.cwd == Path('.'):  # registry entry had no cwd — no transcript folder to read
            return session
        summary = session_summary(str(session.cwd), session.name, self.projects)
        return replace(session, summary=summary)

    def _launch(
        self, cwd: Path, args: Sequence[str], exclusive: bool, env: Mapping[str, str] = {}
    ) -> subprocess.CompletedProcess[bytes]:
        """Run ``claude <args>`` in ``cwd``; under ``exclusive``, refuse if one is already live.

        ``env`` is overlaid on the parent environment, the overlay winning — a captain
        session launching ``ch goal start`` carries ``CHIMERA_ROLE=captain`` itself, and
        the child must get ``agent``.
        """
        if not cwd.is_dir():
            raise FileNotFoundError(cwd)
        if exclusive and (running := self.live(cwd)):
            ids = ', '.join(f'{s.id} ({s.status})' for s in running)
            if all(s.parked for s in running):
                raise RuntimeError(f'a parked session owns {cwd}: {ids} — ch wake revives it')
            raise RuntimeError(f'an agent is already live in {cwd}: {ids} — attach or stop it')
        return subprocess.run(
            ['claude', *args], cwd=cwd, check=True, env={**os.environ, **env} if env else None
        )


def _dismiss(client: 'subprocess.Popen[bytes]') -> None:
    """End the throwaway attach client (and its pty children) — never the worker.

    The client's whole group gets SIGTERM (``script`` doesn't reliably forward a
    signal to its pty child); the daemon-owned worker is in neither. A client that
    ignores the term is SIGKILLed — it's ours and holds nothing.
    """
    with suppress(ProcessLookupError):
        os.killpg(client.pid, signal.SIGTERM)
    try:
        client.wait(5)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(client.pid, signal.SIGKILL)


@cache
def _warn_missing_binary() -> None:
    """
    Land the claude-binary-missing WARNING, once per process.

    A claude-less machine legitimately answers every liveness question with "no
    sessions" — but the same symptom is what a broken PATH looks like on a machine
    that *does* have claude, so it's a WARNING (degraded but continuing), not
    silence. Cached because liveness checks fan out (a sweep asks per worktree):
    one line per run carries the triage signal; one per call would be spew.
    """
    logger.warning('agent: claude binary not found, reporting no sessions')


def _tail(stderr: str | None, limit: int = 4000) -> str:
    """The end of a captured stderr — where the error usually lands — trimmed for the log."""
    return (stderr or '').strip()[-limit:]


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


def _lead_args(extra: Sequence[str], model: str | None, context: Path | None) -> list[str]:
    """The ``--model``/``--append-system-prompt-file`` lead shared by session and print argv.

    ``model`` rides as ``--model`` — unless ``extra`` already carries one, in either
    spelling, so an explicit ``-- --model X`` or ``-- --model=X`` passthrough always
    beats the resolved spec. ``context`` rides as ``--append-system-prompt-file``,
    injecting the rendered launch context before turn 1 with the repo left untouched.
    """
    lead: list[str] = []
    if model is not None and not any(
        arg == '--model' or arg.startswith('--model=') for arg in extra
    ):
        lead += ['--model', model]
    if context is not None:
        lead += ['--append-system-prompt-file', str(context)]
    return lead


def _session_args(
    lead: list[str],
    prompt: str | None,
    extra: Sequence[str],
    dangerous: bool,
    model: str | None = None,
    context: Path | None = None,
) -> list[str]:
    """The claude argv tail: ``--bg`` when backgrounding, the lead, passthrough, then prompt.

    The model/context lead is :func:`_lead_args`.

    With ``dangerous`` the session also gets ``--allow-dangerously-skip-permissions`` (unless
    ``extra`` already asks for bypass) so bypass-permissions mode is reachable with shift-tab.
    It's opt-in: enabling bypass *displaces* auto-accept from claude's shift-tab cycle, so the
    everyday default keeps auto-accept and only an explicit request pays that cost. A ``--bg``
    session is an attachable fork, not headless — you cycle after attaching — and the mode's
    availability is decided at *its* launch, so the flag has to ride the background launch too.
    The flag only enables the mode; the autonomous run keeps its resolved mode.
    """
    lead = [*lead, *_lead_args(extra, model, context)]
    allow = (ALLOW_BYPASS,) if dangerous and not _BYPASS_FLAGS.intersection(extra) else ()
    if prompt is not None:
        return ['--bg', *lead, *extra, *allow, prompt]
    return [*lead, *extra, *allow]


def _print_args(
    extra: Sequence[str], model: str | None, context: Path | None, readonly: bool
) -> list[str]:
    """The print-mode argv lead for :meth:`Claude.run`: no ``--bg``, no ``--name``.

    ``readonly`` becomes a single ``--allowedTools=…`` token — the ``=`` form, because
    claude parses the option as variadic and the two-token spelling would swallow the
    trailing prompt positional (verified against the live CLI).
    """
    args = ['-p', '--output-format', 'json', *_lead_args(extra, model, context)]
    if readonly:
        args.append(f'--allowedTools={",".join(READONLY_TOOLS)}')
    return args
