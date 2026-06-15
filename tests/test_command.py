import pytest
from testfixtures import Command, LogCapture, Replacer, TempDir
from typer.main import get_command

from chimera.__main__ import LoggingCommand, app
from chimera.commands.init import init
from chimera.logging import log_action


def _leaf_commands() -> dict[str, object]:
    """Every runnable command in the app, keyed by its path (e.g. ``goal start``)."""
    leaves: dict[str, object] = {}

    def walk(command: object, prefix: str = '') -> None:
        children = getattr(command, 'commands', None)
        if children:
            for name, child in children.items():
                walk(child, f'{prefix} {name}'.strip())
        else:
            leaves[prefix] = command

    walk(get_command(app))
    return leaves


@pytest.mark.parametrize('path', sorted(_leaf_commands()))
def test_every_command_logs_its_action(path: str) -> None:
    # The guard for "every CLI action must be logged": a command that isn't a
    # LoggingCommand silently skips the chokepoint. Adding one without cls=LoggingCommand
    # (directly or via PassthroughCommand) fails here.
    assert isinstance(_leaf_commands()[path], LoggingCommand)


def test_logs_fixture_captures_loguru(logs: LogCapture) -> None:
    log_action('project ls', {})
    logs.check(('INFO', 'project ls'))


def test_command_logs_the_action(command: Command, tmpdir: TempDir, replace: Replacer) -> None:
    replace.in_environ('CHIMERA_WORKSPACE', str(init(tmpdir.path / 'ws')))
    command.run('project', 'ls').check(logging=[('INFO', 'project ls')])
