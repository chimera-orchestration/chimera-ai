import pytest
import yaml
from testfixtures import Command, Replacer, TempDir

from chimera.commands.project.track import track
from chimera.config import NotInWorkspaceError

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


def test_track_cli(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    workspace = tmpdir.makedir('lycia')
    (workspace / 'config.yaml').write_text('kind: workspace\n')
    repo = tmpdir.makedir('myrepo')
    replace.in_environ('CHIMERA_WORKSPACE', str(workspace))
    command.run('project', 'add', str(repo)).check(
        output=f'Added {workspace / "myrepo"}', logging=[('INFO', 'project add')]
    )
    assert (workspace / 'myrepo' / 'config.yaml').is_file()


def test_track_cli_outside_a_workspace(tmpdir: TempDir, command: Command) -> None:
    repo = tmpdir.makedir('myrepo')
    with pytest.raises(NotInWorkspaceError):
        command.run('project', 'add', str(repo))
    assert not (tmpdir.path / 'myrepo' / 'config.yaml').is_file()  # nothing written
