from testfixtures import ShouldRaise, TempDir, compare

from chimera.commands.init import init
from tests.cli import Command, action_logs


def test_init_creates_workspace(tmpdir: TempDir) -> None:
    path = tmpdir / 'myworkspace'
    compare(init(path), expected=path)
    tmpdir.compare(
        ['.git', '.gitignore', 'config.yaml', 'processes', 'roles'],
        path='myworkspace',
        recursive=False,
    )
    compare(tmpdir.read_text('myworkspace/config.yaml'), expected='kind: workspace\n')


def test_init_names_the_captain(tmpdir: TempDir) -> None:
    init(tmpdir / 'ws', captain='pegasus')
    compare(tmpdir.read_text('ws/config.yaml'), expected='kind: workspace\ncaptain: pegasus\n')


def test_init_gitignores_repos_and_worktrees(tmpdir: TempDir) -> None:
    gitignore = (init(tmpdir / 'ws') / '.gitignore').read_text()
    compare(gitignore, expected='*.lock\nservices-running.jsonl\nlogs/\n*/repo/\n*/worktrees/\n')


def test_init_existing_path_raises(tmpdir: TempDir) -> None:
    path = tmpdir / 'existing'
    path.mkdir()
    with ShouldRaise(FileExistsError(path)):
        init(path)


def test_init_cli(tmpdir: TempDir, command: Command) -> None:
    path = tmpdir / 'ws'
    command.run('init', str(path)).check(
        output=f'Initialized workspace at {path}',
        logging=action_logs(
            'init', 'chimera.commands.init.init', {'path': str(path), 'captain': None}
        ),
    )
    assert (path / '.git').is_dir()


def test_init_cli_with_captain(tmpdir: TempDir, command: Command) -> None:
    path = tmpdir / 'ws'
    command.run('init', str(path), '--captain', 'pegasus').check(
        output=f'Initialized workspace at {path}',
        logging=action_logs(
            'init', 'chimera.commands.init.init', {'path': str(path), 'captain': 'pegasus'}
        ),
    )
    compare((path / 'config.yaml').read_text(), expected='kind: workspace\ncaptain: pegasus\n')


def test_init_cli_existing_path(tmpdir: TempDir, command: Command) -> None:
    path = tmpdir / 'existing'
    path.mkdir()
    # raised through typer, which annotates the instance — match the message instead
    with ShouldRaise(FileExistsError, match=str(path)):
        command.run('init', str(path))
