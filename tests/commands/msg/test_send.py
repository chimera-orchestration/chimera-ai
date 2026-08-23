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


def test_send_refuses_bare_manager_with_the_senders_project(tmpdir: TempDir) -> None:
    with ShouldRaise(
        UserError(
            "'manager' is a bare role — its mailbox is a dead letter no session reads; "
            'send to proj@manager'
        )
    ):
        send(
            tmpdir.path,
            sender='proj@g@agent',
            to='manager',
            subject='done',
            body='.',
            kind='message',
            priority='normal',
            re=None,
        )
    tmpdir.compare(expected=())  # refused before the store — no mailbox minted


def test_send_refuses_bare_manager_with_the_shape_outside_a_project(tmpdir: TempDir) -> None:
    with ShouldRaise(
        UserError(
            "'manager' is a bare role — its mailbox is a dead letter no session reads; "
            'send to <project>@manager'
        )
    ):
        send(
            tmpdir.path,
            sender='pegasus',
            to='manager',
            subject='done',
            body='.',
            kind='message',
            priority='normal',
            re=None,
        )


def test_send_refuses_bare_agent_with_the_goal_shape(tmpdir: TempDir) -> None:
    with ShouldRaise(
        UserError(
            "'agent' is a bare role — its mailbox is a dead letter no session reads; "
            'send to proj@<goal>@agent'
        )
    ):
        send(
            tmpdir.path,
            sender='proj@manager',
            to='agent',
            subject='nudge',
            body='.',
            kind='message',
            priority='normal',
            re=None,
        )


def test_send_to_a_bare_persona_still_delivers(tmpdir: TempDir) -> None:
    # A captain's address is a bare persona name — only role tokens refuse.
    ws = tmpdir.path
    send(
        ws,
        sender='proj@manager',
        to='bellerophon',
        subject='escalating',
        body='.',
        kind='escalation',
        priority='normal',
        re=None,
    )
    assert [m.subject for m in mail(ws).inbox('bellerophon')] == ['escalating']


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
        'message': 'comms: send pegasus -> proj@g@agent [message] ping (sent-1)',
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
