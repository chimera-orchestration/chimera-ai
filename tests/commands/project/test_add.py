from giterator import Git
from giterator.testing import Repo
from testfixtures import TempDir, compare

from chimera.commands.project.add import add


def test_add_tracks_an_existing_local_path(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    repo = tmpdir.makedir('myrepo')
    compare(add(workspace, str(repo)), expected=workspace / 'myrepo')
    compare(
        tmpdir.parse('lycia/myrepo/config.yaml'),
        expected={'kind': 'project', 'repo': str(repo.resolve())},
    )


def test_add_clones_a_url_into_the_workspace(tmpdir: TempDir) -> None:
    origin = Repo.make(tmpdir / 'origin')
    origin.commit_content('seed')
    workspace = tmpdir.makedir('lycia')
    compare(add(workspace, f'file://{origin.path}'), expected=workspace / 'origin')
    repo = workspace / 'origin' / 'repo'
    git = Git(repo)
    compare(git('rev-parse', '--is-bare-repository').strip(), expected='true')  # no working tree
    compare(git.branches(), expected=['main'])
    compare(
        git('symbolic-ref', '--short', 'refs/remotes/origin/HEAD').strip(),
        expected='origin/main',
    )
    compare(
        tmpdir.parse('lycia/origin/config.yaml'), expected={'kind': 'project', 'repo': str(repo)}
    )


def test_add_strips_git_suffix_from_the_cloned_name(tmpdir: TempDir) -> None:
    origin = Repo.make(tmpdir / 'thing.git')
    origin.commit_content('seed')
    workspace = tmpdir.makedir('lycia')
    # .git stripped from the project name
    compare(add(workspace, f'file://{origin.path}'), expected=workspace / 'thing')
