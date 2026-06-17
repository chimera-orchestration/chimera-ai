from testfixtures import Command, ShouldRaise, TempDir, compare

from chimera.commands.init import init


def test_init_creates_workspace(tmpdir: TempDir) -> None:
    path = tmpdir / 'myworkspace'
    compare(init(path), expected=path)
    tmpdir.compare(
        ['.git', '.gitignore', 'config.yaml', 'processes'], path='myworkspace', recursive=False
    )
    compare(tmpdir.read_text('myworkspace/config.yaml'), expected='kind: workspace\n')


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
        output=f'Initialized workspace at {path}', logging=[('INFO', 'init')]
    )
    assert (path / '.git').is_dir() is True


def test_init_cli_existing_path(tmpdir: TempDir, command: Command) -> None:
    path = tmpdir / 'existing'
    path.mkdir()
    # raised through typer, which annotates the instance — match the message instead
    with ShouldRaise(FileExistsError, match=str(path)):
        command.run('init', str(path))
