import pytest
from testfixtures import Command, TempDir

from chimera.commands.init import init


def test_init_creates_workspace(tmpdir: TempDir) -> None:
    path = tmpdir.path / 'myworkspace'
    assert init(path) == path
    assert (path / '.git').is_dir()
    assert (path / 'processes').is_dir()
    assert (path / 'config.yaml').read_text() == 'kind: workspace\n'


def test_init_gitignores_repos_and_worktrees(tmpdir: TempDir) -> None:
    gitignore = (init(tmpdir.path / 'ws') / '.gitignore').read_text()
    assert '*/repo/' in gitignore
    assert '*/worktrees/' in gitignore


def test_init_existing_path_raises(tmpdir: TempDir) -> None:
    path = tmpdir.path / 'existing'
    path.mkdir()
    with pytest.raises(FileExistsError):
        init(path)


def test_init_cli(tmpdir: TempDir, command: Command) -> None:
    path = tmpdir.path / 'ws'
    command.run('init', str(path)).check(
        output=f'Initialized workspace at {path}', logging=[('INFO', 'init')]
    )
    assert (path / '.git').is_dir()


def test_init_cli_existing_path(tmpdir: TempDir, command: Command) -> None:
    path = tmpdir.path / 'existing'
    path.mkdir()
    with pytest.raises(FileExistsError):
        command.run('init', str(path))
