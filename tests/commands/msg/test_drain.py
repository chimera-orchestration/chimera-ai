from datetime import datetime, timezone
from pathlib import Path

from testfixtures import Replacer, TempDir

from chimera.commands.msg.drain import as_context, drain
from chimera.commands.msg.store import mail
from chimera.comms import Message
from tests.cli import Command, action_logs

NOON = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
DRAIN = 'chimera.commands.msg.drain.drain'
RECEIVED = {  # the store's INFO line for the received (seeded m1) message
    'level': 'INFO',
    'message': 'comms: receive',
    'msg_id': 'm1',
    'sender': 'p@manager',
    'to': 'p@g@agent',
    'kind': 'message',
    'priority': 'normal',
    'thread': None,
    're': None,
    'severity': None,
    'subject': 'ping',
    'body': '.',
}


def _seed(ws: Path, address: str, id: str, subject: str = 'ping') -> None:
    mail(ws).send(
        Message(
            id=id,
            sender='p@manager',
            to=address,
            kind='message',
            subject=subject,
            body='.',
            ts=NOON,
        )
    )


def test_drain_claims_new_messages_once(tmpdir: TempDir) -> None:
    ws = tmpdir.path
    _seed(ws, 'me@g@agent', 'm1')
    assert [m.subject for m in drain(ws, 'me@g@agent')] == ['ping']
    assert drain(ws, 'me@g@agent') == []  # already claimed


def test_as_context_renders_a_block(tmpdir: TempDir) -> None:
    ws = tmpdir.path
    _seed(ws, 'me@g@agent', 'm1', subject='hi')
    assert as_context(drain(ws, 'me@g@agent')) == (
        'You have new inter-agent mail:\n- from p@manager [message] hi: .'
    )


def test_msg_drain_cli_lists_received(tmpdir: TempDir, command: Command, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    _seed(ws, 'p@g@agent', 'm1')
    start, end = action_logs('msg drain', DRAIN, {'address': 'p@g@agent', 'inject': False})
    command.run('msg', 'drain', 'p@g@agent').check(
        output='m1  from p@manager  [message] ping', logging=[start, RECEIVED, end]
    )


def test_msg_drain_cli_inject_formats_a_block(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    _seed(ws, 'p@g@agent', 'm1')
    start, end = action_logs('msg drain', DRAIN, {'address': 'p@g@agent', 'inject': True})
    command.run('msg', 'drain', 'p@g@agent', '--inject').check(
        output='You have new inter-agent mail:\n- from p@manager [message] ping: .',
        logging=[start, RECEIVED, end],
    )


def test_msg_drain_cli_inject_empty_is_silent(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    command.run('msg', 'drain', 'nobody@x@agent', '--inject').check(
        output='',
        logging=action_logs('msg drain', DRAIN, {'address': 'nobody@x@agent', 'inject': True}),
    )


def test_msg_drain_cli_nothing_to_receive(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    command.run('msg', 'drain', 'nobody@x@agent').check(
        output='Nothing to receive',
        logging=action_logs('msg drain', DRAIN, {'address': 'nobody@x@agent', 'inject': False}),
    )
