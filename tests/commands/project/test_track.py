from pathlib import Path

from testfixtures import Replacer, ShouldRaise, TempDir, compare

from chimera.commands.project.track import track
from tests.cli import Command, action_logs


def test_track_creates_project_layout(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    repo = tmpdir.makedir('myrepo')
    compare(track(workspace, repo), expected=workspace / 'myrepo')
    tmpdir.compare(
        ['config.yaml', 'knowledge/', 'principles/', 'processes/', 'prompts/'], path='lycia/myrepo'
    )
    compare(
        tmpdir.parse('lycia/myrepo/config.yaml'),
        expected={'kind': 'project', 'repo': str(repo.resolve())},
    )


def test_track_is_idempotent(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    repo = tmpdir.makedir('myrepo')
    track(workspace, repo)
    track(workspace, repo)
    compare(
        tmpdir.parse('lycia/myrepo/config.yaml'),
        expected={'kind': 'project', 'repo': str(repo.resolve())},
    )


def test_track_missing_repo_raises(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    with ShouldRaise(FileNotFoundError((tmpdir / 'nope').resolve())):  # track resolves first
        track(workspace, tmpdir / 'nope')


def test_track_repo_not_a_dir_raises(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    repo = tmpdir.write('afile', b'')
    with ShouldRaise(NotADirectoryError(repo.resolve())):  # track resolves first
        track(workspace, repo)


def test_track_cli(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    workspace = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    repo = tmpdir.makedir('myrepo')
    replace.in_environ('CHIMERA_WORKSPACE', str(workspace))
    command.run('project', 'add', str(repo)).check(
        output=f'Added {workspace / "myrepo"}',
        logging=action_logs(
            'project add',
            'chimera.commands.project.add.add',
            {'source': str(repo), 'checkout': None},
        ),
    )
    assert (workspace / 'myrepo' / 'config.yaml').is_file()


def test_track_cli_outside_a_workspace(tmpdir: TempDir, command: Command) -> None:
    repo = tmpdir.makedir('myrepo')
    # a UserError is caught at the chokepoint: one clean line, exit 1, no traceback
    command.run('project', 'add', str(repo)).check(
        output=f'Error: {Path.cwd()} is not inside a Chimera workspace',
        return_code=1,
        logging=action_logs(
            'project add',
            'chimera.commands.project.add.add',
            {'source': str(repo), 'checkout': None},
            error=f'NotInWorkspaceError: {Path.cwd()} is not inside a Chimera workspace',
        ),
    )
    tmpdir.compare(path='myrepo', expected=())  # nothing written into the repo dir
