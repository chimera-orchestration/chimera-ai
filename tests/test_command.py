import pytest
from testfixtures import Replacer, TempDir
from typer.main import get_command

from chimera.__main__ import LoggingCommand, app
from chimera.commands.init import init
from tests.cli import Command, action_logs


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
    assert isinstance(_leaf_commands()[path], LoggingCommand) is True


def test_command_logs_the_action(command: Command, tmpdir: TempDir, replace: Replacer) -> None:
    replace.in_environ('CHIMERA_WORKSPACE', str(init(tmpdir / 'ws')))
    command.run('project', 'ls').check(
        logging=action_logs('project ls', 'chimera.commands.project.ls.projects', {})
    )


def test_unknown_project_is_a_clean_error(
    command: Command, tmpdir: TempDir, replace: Replacer
) -> None:
    workspace = init(tmpdir / 'ws')
    tmpdir.dump('ws/chimera/config.yaml', {'kind': 'project', 'repo': '/r'})
    replace.in_environ('CHIMERA_WORKSPACE', str(workspace))
    # a typo'd -p gets a one-line message with a suggestion, not a rich traceback; the action
    # still logs a start line and an ERROR end line carrying the message (but no traceback).
    command.run('-p', 'chimerma', 'ls').check(
        output="Error: no project 'chimerma', did you mean 'chimera'? (available: chimera)",
        return_code=1,
        logging=action_logs(
            'ls',
            'chimera.commands.ls.board',
            {'project': None, 'goal': None},
            error="UnknownProjectError: no project 'chimerma', did you mean 'chimera'? "
            '(available: chimera)',
        ),
    )
