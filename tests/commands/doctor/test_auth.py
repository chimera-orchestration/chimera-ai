import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn

import pytest
from testfixtures import LogCapture, Replacer, TempDir, compare, like
from testfixtures.popen import MockPopen

from chimera.agents import AnyCredentials, Credentials, NoCredentials, UnreadableCredentials
from chimera.commands.doctor import CHECKS, auth
from chimera.commands.doctor.auth import GRACE, REALERT, HarnessAuthCheck, notify
from chimera.commands.doctor.core import Exclusions, Finding
from chimera.commands.init import init
from chimera.commands.msg.store import mail
from chimera.comms import Message
from tests.cli import Command, action_logs

LOGIN = 'run /login in any interactive session'


def _states(replace: Replacer, **states: AnyCredentials) -> dict[str, AnyCredentials]:
    """Pin what the check reads; mutate the returned dict to change it mid-test."""
    holder = dict(states)
    replace.in_module(auth.credential_states, lambda: dict(holder))
    return holder


def _live(replace: Replacer, count: int) -> None:
    replace.in_module(auth.live_sessions, lambda harness: count)


def _notifications(replace: Replacer) -> list[str]:
    sent: list[str] = []
    replace.in_module(auth.notify, sent.append)
    return sent


def _probe(replace: Replacer, dead: bool | None, evidence: str) -> list[str]:
    """Pin the confirmation probe's verdict; the returned list records who was probed."""
    probed: list[str] = []

    def fake(harness: str, workspace: Path) -> tuple[bool | None, str]:
        probed.append(harness)
        return dead, evidence

    replace.in_module(auth.probe_confirms_dead, fake)
    return probed


def _healthy(now: datetime) -> Credentials:
    return Credentials(
        source='kc',
        access_expires=now + timedelta(hours=1),
        refresh_expires=now + timedelta(days=30),
    )


def _refresh_expired(now: datetime) -> Credentials:
    return Credentials(
        source='kc',
        access_expires=now - timedelta(hours=3),
        refresh_expires=now - timedelta(minutes=1),
    )


def _access_expired(now: datetime) -> Credentials:
    return Credentials(
        source='kc',
        access_expires=now - GRACE - timedelta(hours=1),
        refresh_expires=now + timedelta(days=30),
    )


def _run(workspace: Path, exclude: Exclusions | None = None) -> list[Finding]:
    return list(HarnessAuthCheck().run(workspace, fix=False, exclude=exclude or Exclusions()))


def _marker(workspace: Path) -> Path:
    return workspace / 'state' / 'auth-alerts' / 'claude.json'


@pytest.fixture()
def ws(tmpdir: TempDir) -> Path:
    workspace = init(tmpdir / 'lycia', captain='bellerophon')
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': 'unused'})
    return workspace


class TestHarnessAuthCheck:
    def test_registered(self) -> None:
        assert any(check.name == 'harness-auth' for check in CHECKS)

    def test_healthy_is_silent(self, ws: Path, replace: Replacer) -> None:
        now = datetime.now(timezone.utc)
        _states(replace, claude=_healthy(now))
        sent = _notifications(replace)
        compare(_run(ws), expected=[])
        compare(sent, expected=[])
        compare(mail(ws).inbox('bellerophon'), expected=[])

    def test_not_logged_in_alerts(self, ws: Path, replace: Replacer) -> None:
        _states(replace, claude=NoCredentials('no keychain item and no file'))
        sent = _notifications(replace)
        compare(
            _run(ws),
            expected=[
                Finding(
                    'harness-auth',
                    f'claude: not logged in (no keychain item and no file) — {LOGIN}',
                    resolved=False,
                    fixable=False,
                )
            ],
        )
        compare(sent, expected=[f'claude auth is down — fleet agents will fail. {LOGIN}.'])
        for address in ('bellerophon', 'proj@manager'):
            compare(
                mail(ws).inbox(address),
                expected=[
                    like(
                        Message,
                        sender='bellerophon',
                        to=address,
                        kind='escalation',
                        priority='urgent',
                        severity=1,
                        subject='claude auth is down — /login needed',
                    )
                ],
            )

    def test_refresh_token_expired_alerts(self, ws: Path, replace: Replacer) -> None:
        now = datetime.now(timezone.utc)
        _states(replace, claude=_refresh_expired(now))
        _live(replace, 0)  # even an idle fleet cannot re-auth once the refresh token is gone
        probes = _probe(replace, None, '')
        sent = _notifications(replace)
        findings = _run(ws)
        compare(len(findings), expected=1)
        assert 'OAuth refresh token expired' in findings[0].message
        assert 'cannot re-auth' in findings[0].message
        compare(len(sent), expected=1)
        compare(probes, expected=[])  # already definitive locally — no turn spent confirming

    def test_access_expired_with_probe_confirming_alerts(self, ws: Path, replace: Replacer) -> None:
        now = datetime.now(timezone.utc)
        _states(replace, claude=_access_expired(now))
        _live(replace, 2)
        probes = _probe(replace, True, 'Not logged in · Please run /login')
        sent = _notifications(replace)
        findings = _run(ws)
        compare(len(findings), expected=1)
        assert 'OAuth access token expired' in findings[0].message
        assert '2 session(s) live' in findings[0].message
        assert (
            'a live probe confirmed auth is dead (Not logged in · Please run /login)'
            in findings[0].message
        )
        compare(len(sent), expected=1)
        compare(probes, expected=['claude'])

    def test_access_expired_but_probe_authenticates_is_healthy(
        self, ws: Path, replace: Replacer
    ) -> None:
        # the 2026-07-13 false-positive shape: sessions resident but idle across expiry —
        # the probe's real turn (which itself refreshes the store) is what settles it
        now = datetime.now(timezone.utc)
        _states(replace, claude=_access_expired(now))
        _live(replace, 2)
        _probe(replace, False, '')
        sent = _notifications(replace)
        compare(_run(ws), expected=[])
        compare(sent, expected=[])

    def test_access_expired_but_probe_cannot_tell_reports_without_paging(
        self, ws: Path, replace: Replacer
    ) -> None:
        now = datetime.now(timezone.utc)
        _states(replace, claude=_access_expired(now))
        _live(replace, 2)
        _probe(replace, None, 'probe timed out after 120s')
        sent = _notifications(replace)
        findings = _run(ws)
        compare(len(findings), expected=1)
        assert 'could not confirm (probe timed out after 120s) — not alerting' in (
            findings[0].message
        )
        compare(sent, expected=[])
        compare(mail(ws).inbox('bellerophon'), expected=[])

    def test_access_expired_while_idle_is_healthy(self, ws: Path, replace: Replacer) -> None:
        # nothing is running, so nothing is dying: the next use refreshes and heals
        now = datetime.now(timezone.utc)
        _states(replace, claude=_access_expired(now))
        _live(replace, 0)
        probes = _probe(replace, True, 'never consulted')
        sent = _notifications(replace)
        compare(_run(ws), expected=[])
        compare(sent, expected=[])
        compare(probes, expected=[])  # no live sessions → nothing dying → no turn spent

    def test_access_expired_within_grace_is_healthy(self, ws: Path, replace: Replacer) -> None:
        # refresh is lazy — a just-expired token only means no call happened yet
        now = datetime.now(timezone.utc)
        _states(
            replace,
            claude=Credentials(
                source='kc',
                access_expires=now - GRACE + timedelta(minutes=5),
                refresh_expires=now + timedelta(days=30),
            ),
        )
        _live(replace, 5)
        sent = _notifications(replace)
        compare(_run(ws), expected=[])
        compare(sent, expected=[])

    def test_unreadable_reports_but_never_alerts(self, ws: Path, replace: Replacer) -> None:
        _states(replace, claude=UnreadableCredentials('keychain locked'))
        sent = _notifications(replace)
        _marker(ws).parent.mkdir(parents=True)
        _marker(ws).write_text('{"signature": "s", "alerted": "2026-01-01T00:00:00+00:00"}')
        compare(
            _run(ws),
            expected=[
                Finding(
                    'harness-auth',
                    'claude: credential state unreadable — keychain locked',
                    resolved=False,
                    fixable=False,
                )
            ],
        )
        compare(sent, expected=[])
        compare(mail(ws).inbox('bellerophon'), expected=[])
        # cannot-tell neither alerts nor clears: the outage may still be in flight behind it
        assert _marker(ws).exists()

    def test_repeat_alert_suppressed(self, ws: Path, replace: Replacer) -> None:
        _states(replace, claude=NoCredentials('gone'))
        sent = _notifications(replace)
        _run(ws)
        _run(ws)
        compare(len(sent), expected=1)
        compare(len(mail(ws).inbox('bellerophon')), expected=1)

    def test_realerts_once_stale(self, ws: Path, replace: Replacer) -> None:
        _states(replace, claude=NoCredentials('gone'))
        sent = _notifications(replace)
        _run(ws)
        data = json.loads(_marker(ws).read_text())
        stale = datetime.now(timezone.utc) - REALERT - timedelta(minutes=1)
        data['alerted'] = stale.isoformat()
        _marker(ws).write_text(json.dumps(data))
        _run(ws)
        compare(len(sent), expected=2)

    def test_changed_problem_realerts_immediately(self, ws: Path, replace: Replacer) -> None:
        now = datetime.now(timezone.utc)
        states = _states(replace, claude=_access_expired(now))
        _live(replace, 2)
        _probe(replace, True, 'Not logged in')
        sent = _notifications(replace)
        _run(ws)
        states['claude'] = _refresh_expired(now)  # the outage escalated
        _run(ws)
        compare(len(sent), expected=2)

    def test_healthy_clears_marker(self, ws: Path, replace: Replacer) -> None:
        now = datetime.now(timezone.utc)
        states = _states(replace, claude=NoCredentials('gone'))
        _notifications(replace)
        _run(ws)
        assert _marker(ws).exists()
        states['claude'] = _healthy(now)
        _run(ws)
        assert not _marker(ws).exists()

    def test_excluded_finding_does_not_alert(self, ws: Path, replace: Replacer) -> None:
        _states(replace, claude=NoCredentials('gone'))
        sent = _notifications(replace)
        findings = _run(ws, Exclusions(tokens=('harness-auth',)))
        compare(len(findings), expected=1)  # the driver drops them; the check still reports
        compare(sent, expected=[])
        compare(mail(ws).inbox('bellerophon'), expected=[])

    def test_sender_is_the_stamped_session(self, ws: Path, replace: Replacer) -> None:
        replace.in_environ('CHIMERA_SESSION', 'chimera-ai@auth-watchdog@agent')
        _states(replace, claude=NoCredentials('gone'))
        _notifications(replace)
        _run(ws)
        compare(
            mail(ws).inbox('bellerophon'),
            expected=[like(Message, sender='chimera-ai@auth-watchdog@agent')],
        )

    def test_expired_refresh_token_short_circuits_the_probe(
        self, ws: Path, replace: Replacer
    ) -> None:
        # both tokens expired: the refresh expiry is already definitive, so no turn is
        # spent confirming the access side — one alert, one finding
        now = datetime.now(timezone.utc)
        _states(
            replace,
            claude=Credentials(
                source='kc',
                access_expires=now - timedelta(hours=3),
                refresh_expires=now - timedelta(hours=2),
            ),
        )
        _live(replace, 1)
        probes = _probe(replace, True, 'never consulted')
        sent = _notifications(replace)
        findings = _run(ws)
        compare(len(findings), expected=1)
        assert 'refresh token expired' in findings[0].message
        compare(len(sent), expected=1)
        compare(probes, expected=[])

    def test_storeless_harness_is_invisible(self, ws: Path, replace: Replacer) -> None:
        _states(replace)
        compare(_run(ws), expected=[])


class TestNotify:
    def test_runs_osascript(self, replace: Replacer) -> None:
        Popen = MockPopen()
        replace.in_module(subprocess.Popen, Popen)
        Popen.set_default()
        notify('auth is down — fleet agents will fail')  # em-dash must ride through unescaped
        compare(
            Popen.all_calls[0].args[0],
            expected=[
                'osascript',
                '-e',
                'display notification "auth is down — fleet agents will fail" '
                'with title "Chimera" sound name "Basso"',
            ],
        )

    def test_failure_warns_and_continues(self, replace: Replacer, full_logs: LogCapture) -> None:
        Popen = MockPopen()
        replace.in_module(subprocess.Popen, Popen)
        Popen.set_default(returncode=1, stderr=b'no can do')
        notify('hello')
        assert 'notification failed' in str(full_logs)

    def test_no_osascript_is_fine(self, replace: Replacer, full_logs: LogCapture) -> None:
        def missing(*args: object, **kw: object) -> NoReturn:
            raise FileNotFoundError('osascript')

        replace(target=subprocess.run, container=subprocess, name='run', replacement=missing)
        notify('hello')
        assert 'notification skipped' in str(full_logs)


def test_doctor_cli_reports_the_auth_finding(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = init(tmpdir / 'lycia', captain='bellerophon')
    # unreadable: a real finding with no alert side effects, so the log lines stay deterministic
    _states(replace, claude=UnreadableCredentials('keychain locked'))
    message = 'harness-auth: claude: credential state unreadable — keychain locked'
    start, end = action_logs(
        'doctor',
        'chimera.commands.doctor.doctor',
        {
            'path': str(ws),
            'fix': False,
            'check': ('harness-auth',),
            'exclude': (),
            'verbose': False,
        },
    )
    command.run('doctor', str(ws), '-c', 'harness-auth').check(
        output=(
            '[harness-auth] (needs attention) claude: credential state unreadable — keychain locked'
        ),
        return_code=1,
        logging=[
            start,
            {'level': 'INFO', 'message': 'harness-auth: checked', 'findings': 1},
            {'level': 'ERROR', 'message': message, 'fixable': False, 'resolved': False},
            end,
        ],
    )


class _FakeAgent:
    """Just enough Agent for the registry-facing seams."""

    def __init__(
        self, state: AnyCredentials | None = None, live: int = 0, turn: Exception | None = None
    ) -> None:
        self._state = state
        self._live = live
        self._turn = turn
        self.runs: list[tuple[Path, str, str]] = []

    def credentials(self) -> AnyCredentials | None:
        return self._state

    def live(self, cwd: Path | None = None) -> list[object]:
        return [object()] * self._live

    def run(self, cwd: Path, name: str, prompt: str, **kw: object) -> str:
        self.runs.append((cwd, name, prompt))
        if self._turn is not None:
            raise self._turn
        return 'pong'


def _agents(replace: Replacer, **agents: _FakeAgent) -> None:
    replace(target=auth.AGENTS, container=auth, name='AGENTS', replacement=agents)


def test_credential_states_reads_every_harness_and_skips_the_storeless(
    replace: Replacer,
) -> None:
    state = NoCredentials('gone')
    _agents(replace, fake=_FakeAgent(state), quiet=_FakeAgent(None))
    compare(auth.credential_states(), expected={'fake': state})


def test_live_sessions_counts_the_harness(replace: Replacer) -> None:
    _agents(replace, fake=_FakeAgent(None, live=3))
    compare(auth.live_sessions('fake'), expected=3)


def _turn_failure(stdout: str = '', stderr: str = '') -> subprocess.CalledProcessError:
    return subprocess.CalledProcessError(1, ['claude'], output=stdout, stderr=stderr)


class TestProbeConfirmsDead:
    def test_a_successful_turn_means_auth_works(self, tmpdir: TempDir, replace: Replacer) -> None:
        agent = _FakeAgent()
        _agents(replace, fake=agent)
        compare(auth.probe_confirms_dead('fake', tmpdir.path), expected=(False, ''))
        compare(
            agent.runs, expected=[(tmpdir.path, 'harness-auth: probe', 'Reply with exactly: pong')]
        )

    def test_an_auth_shaped_failure_is_definitive(self, tmpdir: TempDir, replace: Replacer) -> None:
        # the exact wording the 2026-07-13 sessions saw
        _agents(
            replace, fake=_FakeAgent(turn=_turn_failure(stdout='Not logged in · Please run /login'))
        )
        compare(
            auth.probe_confirms_dead('fake', tmpdir.path),
            expected=(True, 'Not logged in · Please run /login'),
        )

    def test_auth_signs_are_found_in_stderr_case_insensitively(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        _agents(replace, fake=_FakeAgent(turn=_turn_failure(stderr='OAuth token has expired')))
        compare(
            auth.probe_confirms_dead('fake', tmpdir.path),
            expected=(True, 'OAuth token has expired'),
        )

    def test_any_other_failure_cannot_tell(self, tmpdir: TempDir, replace: Replacer) -> None:
        _agents(replace, fake=_FakeAgent(turn=_turn_failure(stderr='getaddrinfo ENOTFOUND api')))
        compare(
            auth.probe_confirms_dead('fake', tmpdir.path),
            expected=(None, 'getaddrinfo ENOTFOUND api'),
        )

    def test_a_timeout_cannot_tell(self, tmpdir: TempDir, replace: Replacer) -> None:
        _agents(replace, fake=_FakeAgent(turn=subprocess.TimeoutExpired(['claude'], 120)))
        compare(
            auth.probe_confirms_dead('fake', tmpdir.path),
            expected=(None, 'probe timed out after 120s'),
        )

    def test_a_missing_binary_cannot_tell(self, tmpdir: TempDir, replace: Replacer) -> None:
        _agents(replace, fake=_FakeAgent(turn=FileNotFoundError('claude')))
        compare(
            auth.probe_confirms_dead('fake', tmpdir.path),
            expected=(None, 'probe could not run (claude)'),
        )


def test_corrupt_marker_never_suppresses(ws: Path, replace: Replacer) -> None:
    # when in doubt, be loud
    _states(replace, claude=NoCredentials('gone'))
    sent = _notifications(replace)
    _marker(ws).parent.mkdir(parents=True)
    _marker(ws).write_text('not json')
    _run(ws)
    compare(len(sent), expected=1)


def test_captain_name_shorthand_and_full_form(tmpdir: TempDir, replace: Replacer) -> None:
    _states(replace, claude=NoCredentials('gone'))
    _notifications(replace)
    ws = init(tmpdir / 'lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace', 'captain': {'name': 'pegasus'}})
    _run(ws)
    compare(mail(ws).inbox('pegasus'), expected=[like(Message, to='pegasus', sender='pegasus')])


def test_captain_defaults_when_the_config_never_named_one(
    tmpdir: TempDir, replace: Replacer
) -> None:
    _states(replace, claude=NoCredentials('gone'))
    _notifications(replace)
    ws = init(tmpdir / 'lycia')  # no captain: key at all
    _run(ws)
    compare(mail(ws).inbox('captain'), expected=[like(Message, to='captain')])
