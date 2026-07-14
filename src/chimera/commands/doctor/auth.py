"""The harness-auth check: fleet-killing OAuth expiry, detected locally and alerted loudly.

Harness OAuth expiry kills every agent on the machine at once, silently — sessions die
with "Not logged in" and nothing pages anyone. One interactive ``/login`` heals it, so
the entire cost of an outage is discovery latency; this check exists to make that
latency one doctor run (a watchdog cron, a manager's stand-up sweep) instead of a night.

The everyday probe is :meth:`chimera.agents.Agent.credentials` — a local read of the
harness's own credential store, no network, no model call — so *cannot reach* can never
be mistaken for *not authorized*: an unreadable store (locked keychain, malformed JSON)
is reported but never alerts. No credentials at all, or a refresh token past its
expiry, are definitive from the store alone and alert straight away.

The one ambiguous local state gets a live confirmation instead of a guess: an access
token expired beyond :data:`GRACE` *while sessions are live* can mean refresh is
failing (a working fleet refreshes on use) — but the 2026-07-13 outage forensics show
auth-dead sessions staying **resident and idle** (wake handlers silently die; ``ch ls``
keeps listing the session), so live-but-idle is exactly the false positive. Only then
does the check spend one :data:`PROBE_MODEL` ping (:func:`probe_confirms_dead`): the
turn succeeding proves auth works (and itself refreshes the store, healing the stale
expiry); failing with auth-shaped output ("Not logged in", "/login") is definitive
death; any other failure — network, timeout — is *cannot tell* and reports without
alerting. With no sessions live an expired access token just means idle — the next use
refreshes and heals, so it isn't even a finding. (Plugin-MCP token expiry is a separate
layer with its own wording and healing — deliberately out of scope here.)

Alerting is the loud path: a macOS notification (best-effort, never breaks the run)
plus urgent escalation mail to the captain and every project manager. A marker under
``state/auth-alerts/`` debounces repeats — the same outage re-alerts only every
:data:`REALERT` — and a healthy probe clears it, so the next outage alerts immediately.

Never ``--fix``-able: only a human typing ``/login`` in an interactive session heals
OAuth (and even that took three attempts on 2026-07-13), so the findings signpost it
and stop.
"""

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

from chimera.agents import AnyCredentials, Credentials, NoCredentials, UnreadableCredentials
from chimera.agents.registry import AGENTS
from chimera.commands.doctor.core import Exclusions, Finding, iter_project_dirs, read_raw
from chimera.commands.msg.store import mail
from chimera.comms import compose

GRACE = timedelta(hours=1)
"""How long past access-token expiry is still healthy: refresh is lazy (on use), so a
just-expired token on a busy machine only means no call happened yet this run."""

REALERT = timedelta(hours=2)
"""How often an unchanged, still-broken state re-alerts — loud, but not a flood."""

LOGIN = 'run /login in any interactive session'

PROBE_MODEL = 'haiku'
"""The cheapest model for the confirmation ping — one tiny turn, only ever spent on the
ambiguous expired-while-live state, never on a healthy run."""

PROBE_TIMEOUT = 120.0
"""Seconds before the confirmation ping counts as *cannot tell* (never as dead)."""

_AUTH_SIGNS = ('not logged in', '/login', 'authentication', 'oauth')
"""Case-insensitive fragments that mark a failed probe as auth-shaped — the 2026-07-13
sessions saw 'Not logged in · Please run /login'. Anything else (DNS, timeouts, a full
disk) must stay *cannot tell*."""


def credential_states() -> dict[str, AnyCredentials]:
    """Each registered harness's locally-read credential state, skipping the storeless.

    The seam the check (and the CLI tests' inerting fixture) replaces: everything the
    check concludes flows from this one read.
    """
    states: dict[str, AnyCredentials] = {}
    for name, agent in sorted(AGENTS.items()):
        state = agent.credentials()
        if state is not None:
            states[name] = state
    return states


def live_sessions(harness: str) -> int:
    """How many of ``harness``'s sessions are live anywhere on the machine right now."""
    return len(AGENTS[harness].live())


def probe_confirms_dead(harness: str, workspace: Path) -> tuple[bool | None, str]:
    """One live :data:`PROBE_MODEL` turn: is ``harness`` actually able to authenticate?

    ``(False, '')`` — the turn ran, auth works (and the call itself refreshed the
    store). ``(True, evidence)`` — the turn failed auth-shaped: definitive death.
    ``(None, detail)`` — the turn failed some other way (network, timeout, no binary):
    *cannot tell*, which callers must never alert on.
    """
    agent = AGENTS[harness]
    try:
        agent.run(
            workspace,
            f'{HarnessAuthCheck.name}: probe',
            'Reply with exactly: pong',
            model=PROBE_MODEL,
            timeout=PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None, f'probe timed out after {PROBE_TIMEOUT:g}s'
    except FileNotFoundError as error:
        return None, f'probe could not run ({error})'
    except subprocess.CalledProcessError as error:
        evidence = ' '.join(f'{error.stdout or ""} {error.stderr or ""}'.split())[-300:]
        if any(sign in evidence.lower() for sign in _AUTH_SIGNS):
            return True, evidence
        return None, evidence
    return False, ''


class HarnessAuthCheck:
    """Every registered harness's OAuth credentials are live; a dead fleet alerts loudly."""

    name = 'harness-auth'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> list[Finding]:
        now = datetime.now(timezone.utc)
        findings: list[Finding] = []
        for harness, state in credential_states().items():
            if isinstance(state, UnreadableCredentials):
                # cannot tell is never not-authorized: report, never alert
                findings.append(
                    Finding(
                        self.name,
                        f'{harness}: credential state unreadable — {state.detail}',
                        resolved=False,
                        fixable=False,
                    )
                )
                continue
            alertable, unconfirmed = _assess(harness, state, workspace, now)
            if not alertable and not unconfirmed:
                _clear_marker(workspace, harness)  # fully healthy: the next outage alerts fresh
                continue
            paging = [p for p in alertable if not exclude.matches(self.name, p)]
            if paging:
                _alert(workspace, harness, paging, now)
            findings.extend(
                Finding(self.name, message, resolved=False, fixable=False)
                for message in (*alertable, *unconfirmed)
            )
        return findings


def _assess(
    harness: str, state: Credentials | NoCredentials, workspace: Path, now: datetime
) -> tuple[list[str], list[str]]:
    """``state``'s auth failures as ``(alertable, unconfirmed)`` finding messages.

    Alertable failures are definitive — absent credentials, an expired refresh token,
    or an expired access token whose live confirmation probe failed auth-shaped.
    Unconfirmed ones report without paging: the probe couldn't tell (network, timeout).
    Both empty means healthy.
    """
    if isinstance(state, NoCredentials):
        return [f'{harness}: not logged in ({state.detail}) — {LOGIN}'], []
    alertable = []
    if state.refresh_expires is not None and state.refresh_expires <= now:
        alertable.append(
            f'{harness}: OAuth refresh token expired {_stamp(state.refresh_expires)} '
            f'({state.source}) — the fleet cannot re-auth; {LOGIN}'
        )
    stale = state.access_expires is not None and state.access_expires <= now - GRACE
    if not stale or alertable:
        return alertable, []  # fresh access token, or already definitive — nothing to confirm
    live = live_sessions(harness)
    if not live:
        return [], []  # idle, not dying: the next use refreshes and heals
    expired = (
        f'{harness}: OAuth access token expired {_stamp(state.access_expires)} '
        f'({state.source}) with {live} session(s) live'
    )
    dead, evidence = probe_confirms_dead(harness, workspace)
    if dead:
        # auth-dead sessions stay resident and idle (the 2026-07-13 mode), so the
        # probe's verdict, not liveness, is what makes this definitive
        return [f'{expired} — a live probe confirmed auth is dead ({evidence}); {LOGIN}'], []
    if dead is None:
        return [], [f'{expired}, but a live probe could not confirm ({evidence}) — not alerting']
    logger.bind(harness=harness).info(
        f'{HarnessAuthCheck.name}: stale expiry but the probe authenticated — healthy'
    )
    return [], []


def _stamp(moment: datetime) -> str:
    return f'{moment.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC'


def _marker(workspace: Path, harness: str) -> Path:
    return workspace / 'state' / 'auth-alerts' / f'{harness}.json'


def _clear_marker(workspace: Path, harness: str) -> None:
    _marker(workspace, harness).unlink(missing_ok=True)


def _suppressed(marker: Path, signature: str, now: datetime) -> bool:
    """Whether this exact broken state already alerted within :data:`REALERT`.

    A marker that doesn't parse never suppresses — when in doubt, be loud.
    """
    if not marker.exists():
        return False
    try:
        data = json.loads(marker.read_text())
        alerted = datetime.fromisoformat(data['alerted'])
        return data['signature'] == signature and now - alerted < REALERT
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return False


def _alert(workspace: Path, harness: str, problems: list[str], now: datetime) -> None:
    """Page the humans: notification plus urgent mail to the captain and every manager.

    Debounced by a marker under ``state/auth-alerts/`` keyed on the exact problem set,
    so an unchanged outage re-alerts only every :data:`REALERT` while a *changed* one
    (access-expired escalating to refresh-expired) alerts straight through.
    """
    marker = _marker(workspace, harness)
    signature = '\n'.join(problems)
    if _suppressed(marker, signature, now):
        logger.bind(harness=harness).info(f'{HarnessAuthCheck.name}: alert suppressed (recent)')
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({'signature': signature, 'alerted': now.isoformat()}))
    notify(f'{harness} auth is down — fleet agents will fail. {LOGIN}.')
    subject = f'{harness} auth is down — /login needed'
    body = '\n'.join((*problems, '', f'Heal it: {LOGIN} on the fleet machine.'))
    # CHIMERA_SESSION when a launcher stamped this session's address, else the captain —
    # not chimera.context.caller, which needs a config too healthy for doctor to assume
    sender = os.environ.get('CHIMERA_SESSION') or _captain(workspace)
    store = mail(workspace)
    recipients = [_captain(workspace)] + [
        f'{project.name}@manager' for project in iter_project_dirs(workspace)
    ]
    for to in recipients:
        # even the sender's own address gets one — an alert skipped is an alert missed
        store.send(
            compose(
                sender=sender,
                to=to,
                kind='escalation',
                priority='urgent',
                severity=1,
                subject=subject,
                body=body,
            )
        )
    logger.bind(harness=harness, recipients=recipients).error(
        f'{HarnessAuthCheck.name}: alerted — {harness} auth is down'
    )


def _captain(workspace: Path) -> str:
    """The captain's persona name, read leniently — doctor runs on legacy configs too."""
    captain = (read_raw(workspace) or {}).get('captain')
    if isinstance(captain, str):
        return captain
    if isinstance(captain, dict) and isinstance(captain.get('name'), str):
        return captain['name']
    return 'captain'


def notify(message: str) -> None:
    """Fire a macOS notification — best-effort and logged, never failing the check.

    Not on a mac (no ``osascript``) it just logs: mail is the cross-platform channel.
    """
    # dumps is only for its double-quote escaping; its default ensure_ascii would render
    # an em-dash as a \u escape, which AppleScript rejects (verified on a live banner)
    quoted = json.dumps(message, ensure_ascii=False)
    script = f'display notification {quoted} with title "Chimera" sound name "Basso"'
    try:
        subprocess.run(['osascript', '-e', script], capture_output=True, text=True, check=True)
    except FileNotFoundError:
        logger.info(f'{HarnessAuthCheck.name}: no osascript, notification skipped')
    except subprocess.CalledProcessError as error:
        logger.bind(stderr=error.stderr.strip()).warning(
            f'{HarnessAuthCheck.name}: notification failed'
        )
