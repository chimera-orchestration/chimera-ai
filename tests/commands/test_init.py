import pytest
from testfixtures import TempDir
from typer.testing import CliRunner

from chimera.__main__ import app
from chimera.commands.init import init

runner = CliRunner()


def test_init_creates_workspace(tmpdir: TempDir) -> None:
    path = tmpdir.path / 'myworkspace'
    assert init(path) == path
    assert (path / '.git').is_dir()
    assert (path / 'processes').is_dir()
    assert (path / '.beads').is_dir()


def test_init_existing_path_raises(tmpdir: TempDir) -> None:
    path = tmpdir.path / 'existing'
    path.mkdir()
    with pytest.raises(FileExistsError):
        init(path)


def test_init_cli(tmpdir: TempDir) -> None:
    path = tmpdir.path / 'ws'
    result = runner.invoke(app, ['init', str(path)])
    assert result.exit_code == 0
    assert f'Initialized workspace at {path}' in result.output
    assert (path / '.git').is_dir()


def test_init_cli_existing_path(tmpdir: TempDir) -> None:
    path = tmpdir.path / 'existing'
    path.mkdir()
    result = runner.invoke(app, ['init', str(path)])
    assert result.exit_code != 0
