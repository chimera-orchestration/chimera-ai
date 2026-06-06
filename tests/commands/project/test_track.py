import pytest
import yaml
from testfixtures import TempDir
from typer.testing import CliRunner

from chimera.__main__ import app
from chimera.commands.project.track import track

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
    assert config == {'kind': 'project', 'repo': str(repo.resolve())}


def test_track_is_idempotent(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    repo = tmpdir.makedir('myrepo')
    track(workspace, repo)
    project = track(workspace, repo)
    assert yaml.safe_load((project / 'config.yaml').read_text()) == {
        'kind': 'project',
        'repo': str(repo.resolve()),
    }


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
    (workspace / 'config.yaml').write_text('kind: workspace\n')
    repo = tmpdir.makedir('myrepo')
    monkeypatch.chdir(workspace)
    result = runner.invoke(app, ['project', 'add', str(repo)])
    assert result.exit_code == 0
    assert (workspace / 'myrepo' / 'config.yaml').is_file()


def test_track_cli_outside_a_workspace(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmpdir.makedir('myrepo')
    monkeypatch.chdir(tmpdir.path)  # no workspace config.yaml anywhere above
    result = runner.invoke(app, ['project', 'add', str(repo)])
    assert result.exit_code != 0
    assert not (tmpdir.path / 'myrepo' / 'config.yaml').is_file()  # nothing written
