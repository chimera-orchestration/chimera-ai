from datetime import datetime, timezone
from pathlib import Path

from testfixtures import Replacer, TempDir, compare

from chimera.commands.msg.dispose import dispose
from chimera.commands.msg.inbox import inbox
from chimera.commands.msg.store import mail
from chimera.comms import Message
from tests.cli import Command, action_logs

NOON = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
DISPOSE = 'chimera.commands.msg.dispose.dispose'
DISPOSED = {  # the store's INFO line for a disposed message (the seeded m1)
    'level': 'INFO',
    'message': 'comms: dispose p@manager -> p@g@agent [request] do (m1)',
    'msg_id': 'm1',
    'sender': 'p@manager',
    'to': 'p@g@agent',
    'kind': 'request',
    'priority': 'normal',
    'thread': None,
    're': None,
    'severity': None,
    'subject': 'do',
    'body': '.',
}


def _seed(ws: Path, address: str, id: str) -> None:
    mail(ws).send(
        Message(
            id=id, sender='p@manager', to=address, kind='request', subject='do', body='.', ts=NOON
        )
    )


def test_dispose_retires_a_message(tmpdir: TempDir) -> None:
    ws = tmpdir.path
    _seed(ws, 'me@g@agent', 'm1')
    dispose(ws, 'me@g@agent', 'm1')
    assert inbox(ws, 'me@g@agent', unread_only=False) == []


def test_msg_ack_cli(tmpdir: TempDir, command: Command, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    _seed(ws, 'p@g@agent', 'm1')
    start, end = action_logs('msg ack', DISPOSE, {'message_id': 'm1', 'address': 'p@g@agent'})
    command.run('msg', 'ack', 'm1', 'p@g@agent').check(
        output='Acked m1', logging=[start, DISPOSED, end]
    )
    assert inbox(ws, 'p@g@agent', unread_only=False) == []


def test_msg_defer_cli(tmpdir: TempDir, command: Command, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    _seed(ws, 'p@g@agent', 'm1')
    start, end = action_logs(
        'msg defer', DISPOSE, {'message_id': 'm1', 'reason': 'later', 'address': 'p@g@agent'}
    )
    command.run('msg', 'defer', 'm1', 'p@g@agent', '--reason', 'later').check(
        output='Deferred m1: later', logging=[start, DISPOSED, end]
    )


def test_msg_ack_cli_on_an_unknown_message_is_a_user_error(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    # a wrong address or id must not print 'Acked' while the real message stays undisposed
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    _seed(ws, 'p@g@agent', 'm1')
    start, end = action_logs(
        'msg ack',
        DISPOSE,
        {'message_id': 'm1', 'address': 'wrong@g@agent'},
        error='MessageNotFoundError: no such message for wrong@g@agent: m1',
    )
    command.run('msg', 'ack', 'm1', 'wrong@g@agent').check(
        output='Error: no such message for wrong@g@agent: m1',
        return_code=1,
        logging=[start, end],
    )
    compare(
        inbox(ws, 'p@g@agent', unread_only=False),
        expected=[
            Message(
                id='m1',
                sender='p@manager',
                to='p@g@agent',
                kind='request',
                subject='do',
                body='.',
                ts=NOON,
            )
        ],
    )
