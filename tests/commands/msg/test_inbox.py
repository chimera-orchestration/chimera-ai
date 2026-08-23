from datetime import datetime, timezone
from pathlib import Path

from testfixtures import Replacer, TempDir

from chimera.commands.msg.inbox import inbox
from chimera.commands.msg.store import mail
from chimera.comms import Message
from tests.cli import Command, action_logs

NOON = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
INBOX = 'chimera.commands.msg.inbox.inbox'


def _seed(ws: Path, address: str, id: str, subject: str, sender: str = 'p@manager') -> None:
    mail(ws).send(
        Message(
            id=id, sender=sender, to=address, kind='message', subject=subject, body='.', ts=NOON
        )
    )


def test_inbox_returns_awaiting_messages(tmpdir: TempDir) -> None:
    ws = tmpdir.path
    _seed(ws, 'me@g@agent', '01', 'hi')
    assert [m.subject for m in inbox(ws, 'me@g@agent', unread_only=False)] == ['hi']


def test_inbox_unread_only_excludes_drained(tmpdir: TempDir) -> None:
    ws = tmpdir.path
    _seed(ws, 'me@g@agent', '01', 'old')
    mail(ws).drain('me@g@agent')  # 01 → cur/
    _seed(ws, 'me@g@agent', '02', 'fresh')
    assert [m.subject for m in inbox(ws, 'me@g@agent', unread_only=True)] == ['fresh']


def test_inbox_on_a_bare_role_mailbox_stays_readable(tmpdir: TempDir) -> None:
    # send refuses bare roles, but a mailbox stranded before that fence must stay readable.
    ws = tmpdir.path
    _seed(ws, 'manager', '01', 'stranded')
    assert [m.subject for m in inbox(ws, 'manager', unread_only=False)] == ['stranded']


def test_msg_inbox_cli_lists(tmpdir: TempDir, command: Command, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    _seed(ws, 'p@g@agent', '01', 'ping')
    start, end = action_logs('msg inbox', INBOX, {'address': 'p@g@agent', 'unread': False})
    found = {  # the one-shot's outcome line: whose inbox, how much found
        'level': 'INFO',
        'message': 'comms: inbox p@g@agent (1)',
        'address': 'p@g@agent',
        'unread_only': False,
        'count': 1,
    }
    command.run('msg', 'inbox', 'p@g@agent').check(
        output='01  p@manager  [message] ping', logging=[start, found, end]
    )


def test_msg_inbox_cli_empty(tmpdir: TempDir, command: Command, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    start, end = action_logs('msg inbox', INBOX, {'address': 'nobody@x@agent', 'unread': False})
    found = {
        'level': 'INFO',
        'message': 'comms: inbox nobody@x@agent (0)',
        'address': 'nobody@x@agent',
        'unread_only': False,
        'count': 0,
    }
    command.run('msg', 'inbox', 'nobody@x@agent').check(
        output='No messages', logging=[start, found, end]
    )
