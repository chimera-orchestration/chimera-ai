import yaml
from testfixtures import Command, Replacer, ShouldRaise, TempDir, compare

from chimera.commands.project.track import track
from chimera.config import NotInWorkspaceError

_DIRS = ('knowledge', 'prompts', 'principles', 'processes')


def test_track_creates_project_layout(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    repo = tmpdir.makedir('myrepo')
    project = track(workspace, repo)
    compare(project, expected=workspace / 'myrepo')
    compare({sub for sub in _DIRS if (project / sub).is_dir()}, expected=set(_DIRS))
    config = yaml.safe_load((project / 'config.yaml').read_text())
    compare(config, expected={'kind': 'project', 'repo': str(repo.resolve())})


def test_track_is_idempotent(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    repo = tmpdir.makedir('myrepo')
    track(workspace, repo)
    project = track(workspace, repo)
    compare(
        yaml.safe_load((project / 'config.yaml').read_text()),
        expected={'kind': 'project', 'repo': str(repo.resolve())},
    )


def test_track_missing_repo_raises(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    with ShouldRaise(FileNotFoundError((tmpdir.path / 'nope').resolve())):  # track resolves first
        track(workspace, tmpdir.path / 'nope')


def test_track_repo_not_a_dir_raises(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    repo = tmpdir.write('afile', b'')
    with ShouldRaise(NotADirectoryError(repo.resolve())):  # track resolves first
        track(workspace, repo)


def test_track_cli(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    workspace = tmpdir.makedir('lycia')
    (workspace / 'config.yaml').write_text('kind: workspace\n')
    repo = tmpdir.makedir('myrepo')
    replace.in_environ('CHIMERA_WORKSPACE', str(workspace))
    command.run('project', 'add', str(repo)).check(
        output=f'Added {workspace / "myrepo"}', logging=[('INFO', 'project add')]
    )
    assert (workspace / 'myrepo' / 'config.yaml').is_file() is True


def test_track_cli_outside_a_workspace(tmpdir: TempDir, command: Command) -> None:
    repo = tmpdir.makedir('myrepo')
    # raised through typer, which annotates the instance — match the message instead
    with ShouldRaise(NotInWorkspaceError, match='is not inside a Chimera workspace'):
        command.run('project', 'add', str(repo))
    assert (tmpdir.path / 'myrepo' / 'config.yaml').is_file() is False  # nothing written
