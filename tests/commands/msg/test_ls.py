from datetime import datetime, timedelta, timezone

from testfixtures import Replacer, TempDir

from chimera.commands.msg.ls import outstanding
from chimera.comms import Comms, compose
from tests.cli import Command, action_logs

NOON = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
FRAME = ('msg ls', 'chimera.commands.msg.ls.outstanding')


def _mail(root):
    return Comms(root / 'state' / 'mail')


def _workspace(tmpdir: TempDir):
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    return tmpdir.path / 'ws'


def test_outstanding_reads_the_workspace_mail(tmpdir: TempDir) -> None:
    ws = tmpdir.path
    _mail(ws).send(
        compose(sender='a@manager', to='b@g@agent', kind='message', subject='hi', body='.')
    )
    assert [(state, m.subject) for state, m in outstanding(ws)] == [('new', 'hi')]


def test_msg_ls_cli_lists_outstanding_messages(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    ws = _workspace(tmpdir)
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    _mail(ws).send(
        compose(sender='proj@manager', to='proj@g@agent', kind='message', subject='ping', body='hi')
    )
    command.run('msg', 'ls').check(
        output='new   proj@manager → proj@g@agent  [message] ping',
        logging=action_logs(*FRAME, {'verbose': False}),
    )


def test_msg_ls_cli_reports_an_empty_mailbox(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    ws = _workspace(tmpdir)
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    command.run('msg', 'ls').check(
        output='No outstanding messages', logging=action_logs(*FRAME, {'verbose': False})
    )


def test_msg_ls_cli_hides_disposed_behind_a_hint(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    ws = _workspace(tmpdir)
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    comms = _mail(ws)
    comms.send(
        compose(
            sender='p@manager', to='p@g@agent', kind='message', subject='live', body='.', now=NOON
        )
    )
    gone = comms.send(
        compose(
            sender='p@manager',
            to='p@g@agent',
            kind='notice',
            subject='gone',
            body='.',
            now=NOON + timedelta(minutes=1),
        )
    )
    comms.dispose('p@g@agent', gone.id)  # → done/, awaiting cleanup
    command.run('msg', 'ls').check(
        output=(
            'new   p@manager → p@g@agent  [message] live\n'
            '(+1 disposed message — ch msg ls -v to show)'
        ),
        logging=action_logs(*FRAME, {'verbose': False}),
    )


def test_msg_ls_cli_verbose_shows_disposed(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    ws = _workspace(tmpdir)
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    comms = _mail(ws)
    comms.send(
        compose(
            sender='p@manager', to='p@g@agent', kind='message', subject='live', body='.', now=NOON
        )
    )
    gone = comms.send(
        compose(
            sender='p@manager',
            to='p@g@agent',
            kind='notice',
            subject='gone',
            body='.',
            now=NOON + timedelta(minutes=1),
        )
    )
    comms.dispose('p@g@agent', gone.id)
    command.run('msg', 'ls', '-v').check(
        output=(
            'new   p@manager → p@g@agent  [message] live\n'
            'done  p@manager → p@g@agent  [notice] gone'
        ),
        logging=action_logs(*FRAME, {'verbose': True}),
    )
