from datetime import datetime

from testfixtures import Replacer, ShouldRaise, TempDir

from chimera.commands.msg.send import send
from chimera.commands.msg.store import mail
from chimera.comms import _new_id
from chimera.config import UserError
from tests.cli import Command, action_logs

SEND = 'chimera.commands.msg.send.send'


def _fixed_id(ts: datetime) -> str:
    return 'sent-1'


def test_send_delivers_to_the_recipient(tmpdir: TempDir) -> None:
    ws = tmpdir.path
    message = send(
        ws,
        sender='a@manager',
        to='b@g@agent',
        subject='hi',
        body='.',
        kind='message',
        priority='normal',
        re=None,
    )
    assert message.to == 'b@g@agent'
    assert [m.subject for m in mail(ws).inbox('b@g@agent')] == ['hi']


def test_send_reply_threads_on_the_original(tmpdir: TempDir) -> None:
    message = send(
        tmpdir.path,
        sender='a',
        to='b',
        subject='re',
        body='.',
        kind='message',
        priority='normal',
        re='root-1',
    )
    assert (message.thread, message.re) == ('root-1', 'root-1')


def test_send_rejects_an_unknown_kind(tmpdir: TempDir) -> None:
    with ShouldRaise(
        UserError("unknown kind 'bogus'; one of message, request, escalation, notice")
    ):
        send(
            tmpdir.path,
            sender='a',
            to='b',
            subject='s',
            body='.',
            kind='bogus',
            priority='normal',
            re=None,
        )


def test_send_rejects_an_unknown_priority(tmpdir: TempDir) -> None:
    with ShouldRaise(UserError("unknown priority 'loud'; one of normal, urgent")):
        send(
            tmpdir.path,
            sender='a',
            to='b',
            subject='s',
            body='.',
            kind='message',
            priority='loud',
            re=None,
        )


def test_msg_send_cli(tmpdir: TempDir, command: Command, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_module(_new_id, _fixed_id)  # deterministic id for the smoke
    start, end = action_logs(
        'msg send',
        SEND,
        {
            'to': 'proj@g@agent',
            'subject': 'ping',
            'body': 'hi',
            'frm': 'pegasus',
            'kind': 'message',
            'priority': 'normal',
            're': None,
        },
    )
    sent = {  # the store's own INFO line: source/dest/content
        'level': 'INFO',
        'message': 'comms: send',
        'msg_id': 'sent-1',
        'sender': 'pegasus',
        'to': 'proj@g@agent',
        'kind': 'message',
        'priority': 'normal',
        'thread': None,
        're': None,
        'severity': None,
        'subject': 'ping',
        'body': 'hi',
    }
    command.run('msg', 'send', 'proj@g@agent', 'ping', 'hi', '--from', 'pegasus').check(
        output='Sent sent-1 to proj@g@agent',
        logging=[start, sent, end],
    )
    assert [m.subject for m in mail(ws).inbox('proj@g@agent')] == ['ping']
