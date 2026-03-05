from typer.testing import CliRunner

from chimera.cli import app
from testfixtures import TempDir

runner = CliRunner()


def test_init_creates_lycia(tmpdir: TempDir) -> None:
    path = tmpdir.path / 'mylycia'
    result = runner.invoke(app, ['init', str(path)])
    assert result.exit_code == 0
    assert path.is_dir()
    assert (path / '.git').is_dir()
    assert (path / 'processes').is_dir()
    assert (path / '.beads').is_dir()


def test_init_path_exists(tmpdir: TempDir) -> None:
    path = tmpdir.path / 'existing'
    path.mkdir()
    result = runner.invoke(app, ['init', str(path)])
    assert result.exit_code == 1
    assert 'already exists' in result.output
