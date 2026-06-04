import pytest
import yaml
from testfixtures import TempDir
from typer.testing import CliRunner

from chimera.__main__ import app
from chimera.commands.track import track

runner = CliRunner()

_DIRS = ('knowledge', 'prompts', 'principles', 'processes')


def test_track_creates_project_layout(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    repo = tmpdir.makedir('myrepo')
    project = track(workspace, repo)
    assert project == workspace / 'myrepo'
    for sub in _DIRS:
        assert (project / sub).is_dir()
    config = yaml.safe_load((project / 'config.yaml').read_text())
    assert config == {'repo': str(repo.resolve())}


def test_track_is_idempotent(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    repo = tmpdir.makedir('myrepo')
    track(workspace, repo)
    project = track(workspace, repo)
    assert yaml.safe_load((project / 'config.yaml').read_text()) == {'repo': str(repo.resolve())}


def test_track_missing_repo_raises(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    with pytest.raises(FileNotFoundError):
        track(workspace, tmpdir.path / 'nope')


def test_track_repo_not_a_dir_raises(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    repo = tmpdir.write('afile', b'')
    with pytest.raises(NotADirectoryError):
        track(workspace, repo)


def test_track_cli(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmpdir.makedir('lycia')
    repo = tmpdir.makedir('myrepo')
    monkeypatch.chdir(workspace)
    result = runner.invoke(app, ['track', str(repo)])
    assert result.exit_code == 0
    assert (workspace / 'myrepo' / 'config.yaml').is_file()
