import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from testfixtures import Replacer, TempDir

from chimera.commands.hook.capture import session_start
from chimera.commands.hook.deliver import deliver
from chimera.commands.msg.dispose import dispose
from chimera.commands.msg.drain import drain
from chimera.commands.msg.store import mail
from chimera.comms import Message
from tests.cli import Command, Run, action_logs, full_capture

BRIDGE = (
    'hook deliver: no archive row for this session — bridged (a backgrounded session '
    're-hosted under a new id) or pre-hook; delivering by cwd address'
)

NOON = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
DELIVER = 'chimera.commands.hook.deliver.deliver'
ADDRESS = 'p@g@agent'


def _seed(ws: Path, id: str, subject: str = 'ping') -> None:
    mail(ws).send(
        Message(
            id=id,
            sender='p@manager',
            to=ADDRESS,
            kind='message',
            subject=subject,
            body='.',
            ts=NOON,
        )
    )


def _ws(tmpdir: TempDir, replace: Replacer) -> Path:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_SESSION', ADDRESS)  # the launcher's address stamp
    return ws


def test_deliver_claims_and_surfaces_new_mail(tmpdir: TempDir, replace: Replacer) -> None:
    ws = _ws(tmpdir, replace)
    _seed(ws, 'm1')
    assert [m.id for m in deliver(ws, 'uuid-1')] == ['m1']
    assert mail(ws).inbox(ADDRESS, unread_only=True) == []  # claimed: new/ → cur/


def test_deliver_surfaces_mail_a_third_party_drained(tmpdir: TempDir, replace: Replacer) -> None:
    ws = _ws(tmpdir, replace)
    _seed(ws, 'm1')
    drain(ws, ADDRESS)  # another process claims it — the hook must not go silent
    assert [m.id for m in deliver(ws, 'uuid-1')] == ['m1']


def test_deliver_does_not_respam_a_session_but_stops_only_at_ack(
    tmpdir: TempDir, replace: Replacer
) -> None:
    ws = _ws(tmpdir, replace)
    _seed(ws, 'm1')
    deliver(ws, 'uuid-1')
    assert deliver(ws, 'uuid-1') == []  # this session already saw it
    assert [m.id for m in deliver(ws, 'uuid-2')] == ['m1']  # unacked → a fresh session still does
    dispose(ws, ADDRESS, 'm1')
    assert deliver(ws, 'uuid-3') == []  # acked → surfacing over


def test_deliver_skips_a_session_archived_without_an_address(
    tmpdir: TempDir, replace: Replacer
) -> None:
    # a one-shot -p run shares its cwd with real conversations: caller(cwd) would hand it
    # their mail, so the archive's no-address fact (capture.addressed) is what gates here
    ws = _ws(tmpdir, replace)
    replace.in_environ('CHIMERA_ROLE', '')
    session_start(ws, 'uuid-p', '/t.jsonl', 'startup', entrypoint='sdk-cli')
    _seed(ws, 'm1')
    assert deliver(ws, 'uuid-p') == []
    assert [m.id for m in deliver(ws, 'uuid-chat')] == ['m1']  # the real session still gets it


def test_verbose_flags_an_unrecorded_bridged_session(tmpdir: TempDir, replace: Replacer) -> None:
    # a session id the archive never saw is the bridge signature; it is still delivered to
    # (fail-open by cwd), and -v records exactly what we need to spot the bridge in the log
    ws = _ws(tmpdir, replace)
    _seed(ws, 'm1')
    with full_capture() as log:
        assert [m.id for m in deliver(ws, 'bridged-uuid', verbose=True)] == ['m1']
    log.check_present(
        {
            'level': 'WARNING',  # a fallback taken — delivering by cwd, archive blind
            'message': BRIDGE,
            'session': 'bridged-uuid',
            'cwd': str(ws),
            'address': ADDRESS,
            'recorded': False,
            'recorded_name': None,
        }
    )


def test_verbose_names_a_session_the_archive_knows(tmpdir: TempDir, replace: Replacer) -> None:
    ws = _ws(tmpdir, replace)
    session_start(ws, 'uuid-1', '/t.jsonl', 'startup')  # recorded, addressed
    _seed(ws, 'm1')
    with full_capture() as log:
        deliver(ws, 'uuid-1', verbose=True)
    log.check_present(
        {
            'level': 'INFO',
            'message': 'hook deliver: session resolved from the archive',
            'session': 'uuid-1',
            'cwd': str(ws),
            'address': ADDRESS,
            'recorded': True,
            'recorded_name': ADDRESS,
        }
    )


def test_no_diagnostic_without_verbose(tmpdir: TempDir, replace: Replacer) -> None:
    ws = _ws(tmpdir, replace)
    _seed(ws, 'm1')
    with full_capture() as log:
        deliver(ws, 'bridged-uuid')  # default: quiet, since it fires every turn
    assert not any(str(e.get('message', '')).startswith('hook deliver:') for e in log.actual())


def test_deliver_outside_a_workspace_is_a_noop(tmpdir: TempDir, replace: Replacer) -> None:
    replace.in_environ('CHIMERA_SESSION', ADDRESS)
    assert deliver(tmpdir.path / 'nowhere', 'uuid-x') == []
    assert not (tmpdir.path / 'nowhere').exists()  # nothing written either


def _run_hook(command: Command, replace: Replacer, ws: Path, session: str, *flags: str) -> Run:
    payload = json.dumps({'cwd': str(ws), 'session_id': session})
    replace(target=sys.stdin, container=sys, name='stdin', replacement=io.StringIO(payload))
    return command.run('hook', 'deliver', *flags)


def test_hook_deliver_cli_injects_a_block(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    ws = _ws(tmpdir, replace)
    _seed(ws, 'm1')
    drain(ws, ADDRESS)  # even a third-party claim doesn't silence the injection
    start, end = action_logs('hook deliver', DELIVER, {'verbose': False})
    delivered = {
        'level': 'INFO',
        'message': 'comms: deliver p@manager -> p@g@agent [message] ping (m1)',
        'session': 'uuid-1',
        'msg_id': 'm1',
        'sender': 'p@manager',
        'to': ADDRESS,
        'kind': 'message',
        'priority': 'normal',
        'thread': None,
        're': None,
        'severity': None,
        'subject': 'ping',
        'body': '.',
    }
    _run_hook(command, replace, ws, 'uuid-1').check(
        output=(
            'You have inter-agent mail; once a message is handled, `ch msg ack <id>` it:\n'
            '- m1 from p@manager [message] ping: .'
        ),
        logging=[start, delivered, end],
    )


def test_hook_deliver_cli_verbose_logs_the_diagnostic(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    # the wrapper's -v wiring: a typo in the option declaration would pass the
    # pure-function tests, which bypass Click entirely
    ws = _ws(tmpdir, replace)
    _seed(ws, 'm1')
    start, end = action_logs('hook deliver', DELIVER, {'verbose': True})
    bridged = {
        'level': 'WARNING',
        'message': BRIDGE,
        'session': 'uuid-1',
        'cwd': str(ws),
        'address': ADDRESS,
        'recorded': False,
        'recorded_name': None,
    }
    received = {  # deliver's own claim of the still-undrained m1: new/ → cur/
        'level': 'INFO',
        'message': 'comms: receive p@manager -> p@g@agent [message] ping (m1)',
        'msg_id': 'm1',
        'sender': 'p@manager',
        'to': ADDRESS,
        'kind': 'message',
        'priority': 'normal',
        'thread': None,
        're': None,
        'severity': None,
        'subject': 'ping',
        'body': '.',
    }
    delivered = {
        'level': 'INFO',
        'message': 'comms: deliver p@manager -> p@g@agent [message] ping (m1)',
        'session': 'uuid-1',
        'msg_id': 'm1',
        'sender': 'p@manager',
        'to': ADDRESS,
        'kind': 'message',
        'priority': 'normal',
        'thread': None,
        're': None,
        'severity': None,
        'subject': 'ping',
        'body': '.',
    }
    _run_hook(command, replace, ws, 'uuid-1', '-v').check(
        output=(
            'You have inter-agent mail; once a message is handled, `ch msg ack <id>` it:\n'
            '- m1 from p@manager [message] ping: .'
        ),
        logging=[start, bridged, received, delivered, end],
    )


def test_hook_deliver_cli_is_silent_once_seen(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    ws = _ws(tmpdir, replace)
    _seed(ws, 'm1')
    deliver(ws, 'uuid-1')
    _run_hook(command, replace, ws, 'uuid-1').check(
        output='', logging=action_logs('hook deliver', DELIVER, {'verbose': False})
    )
