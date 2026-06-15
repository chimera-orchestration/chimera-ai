from testfixtures import Command, LogCapture, Replacer, TempDir

from chimera.commands.init import init
from chimera.logging import log_action


def test_logs_fixture_captures_loguru(logs: LogCapture) -> None:
    log_action('project ls', {})
    logs.check(('INFO', 'project ls'))


def test_command_logs_the_action(command: Command, tmpdir: TempDir, replace: Replacer) -> None:
    replace.in_environ('CHIMERA_WORKSPACE', str(init(tmpdir.path / 'ws')))
    command.run('project', 'ls').check(logging=[('INFO', 'project ls')])
