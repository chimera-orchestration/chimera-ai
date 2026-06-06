import yaml
from giterator import Git
from giterator.testing import Repo
from testfixtures import TempDir

from chimera.commands.project.add import add


def test_add_tracks_an_existing_local_path(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    repo = tmpdir.makedir('myrepo')
    project = add(workspace, str(repo))
    assert project == workspace / 'myrepo'
    config = yaml.safe_load((project / 'config.yaml').read_text())
    assert config == {'kind': 'project', 'repo': str(repo.resolve())}


def test_add_clones_a_url_into_the_workspace(tmpdir: TempDir) -> None:
    origin = Repo.make(tmpdir.path / 'origin')
    origin.commit_content('seed')
    workspace = tmpdir.makedir('lycia')
    project = add(workspace, f'file://{origin.path}')
    assert project == workspace / 'origin'
    repo = project / 'repo'
    assert (repo / '.git').is_dir()  # a real clone landed under repo/
    assert 'main' in Git(repo).branches()
    config = yaml.safe_load((project / 'config.yaml').read_text())
    assert config == {'kind': 'project', 'repo': str(repo)}


def test_add_strips_git_suffix_from_the_cloned_name(tmpdir: TempDir) -> None:
    origin = Repo.make(tmpdir.path / 'thing.git')
    origin.commit_content('seed')
    workspace = tmpdir.makedir('lycia')
    project = add(workspace, f'file://{origin.path}')
    assert project == workspace / 'thing'  # .git stripped from the project name
