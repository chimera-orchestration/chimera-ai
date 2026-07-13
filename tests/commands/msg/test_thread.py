from datetime import datetime, timezone
from pathlib import Path

from testfixtures import Replacer, TempDir

from chimera.commands.msg.store import mail
from chimera.commands.msg.thread import thread
from chimera.comms import Message
from tests.cli import Command, action_logs

NOON = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
THREAD = 'chimera.commands.msg.thread.thread'


def _seed(ws: Path, address: str, id: str, subject: str, root: str | None = None) -> None:
    mail(ws).send(
        Message(
            id=id,
            sender='p@manager',
            to=address,
            kind='message',
            subject=subject,
            body='.',
            ts=NOON,
            thread=root,
            re=root,
        )
    )


def test_thread_gathers_the_conversation(tmpdir: TempDir) -> None:
    ws = tmpdir.path
    _seed(ws, 'p@g@agent', '01', 'root')
    _seed(ws, 'p@g@agent', '02', 'reply', root='01')
    _seed(ws, 'p@g@agent', '03', 'unrelated')
    assert [m.subject for m in thread(ws, 'p@g@agent', '01')] == ['root', 'reply']


def test_msg_thread_cli(tmpdir: TempDir, command: Command, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    _seed(ws, 'p@g@agent', '01', 'root')
    _seed(ws, 'p@g@agent', '02', 'reply', root='01')
    command.run('msg', 'thread', '01', 'p@g@agent').check(
        output=(
            '01  p@manager → p@g@agent  [message] root\n02  p@manager → p@g@agent  [message] reply'
        ),
        logging=action_logs('msg thread', THREAD, {'root': '01', 'address': 'p@g@agent'}),
    )


def test_msg_thread_cli_unknown(tmpdir: TempDir, command: Command, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    command.run('msg', 'thread', 'nope', 'p@g@agent').check(
        output='No such thread',
        logging=action_logs('msg thread', THREAD, {'root': 'nope', 'address': 'p@g@agent'}),
    )
