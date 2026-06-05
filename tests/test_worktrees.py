from giterator import Git
from giterator.testing import Repo
from testfixtures import TempDir

from chimera.worktrees import is_dirty, is_merged, registered_worktrees


def _seeded_repo(tmpdir: TempDir) -> Repo:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    return repo


def test_registered_worktrees_lists_repo_and_added(tmpdir: TempDir) -> None:
    repo = _seeded_repo(tmpdir)
    git = Git(repo.path)
    wt = tmpdir.path / 'wt'
    git('worktree', 'add', '-b', 'side', str(wt), 'main')
    registered = registered_worktrees(git)
    assert repo.path.resolve() in registered
    assert wt.resolve() in registered


def test_is_merged_true_for_ancestor(tmpdir: TempDir) -> None:
    repo = _seeded_repo(tmpdir)
    git = Git(repo.path)
    git('branch', 'feature', 'HEAD')  # points at HEAD → an ancestor of it
    assert is_merged(git, 'feature')


def test_is_merged_false_for_branch_ahead(tmpdir: TempDir) -> None:
    repo = _seeded_repo(tmpdir)
    git = Git(repo.path)
    git('checkout', '-q', '-b', 'feature')
    repo.commit_content('ahead')  # feature now ahead of main
    git('checkout', '-q', 'main')
    assert not is_merged(git, 'feature')


def test_is_dirty(tmpdir: TempDir) -> None:
    repo = _seeded_repo(tmpdir)
    assert not is_dirty(repo.path)
    (repo.path / 'scratch.txt').write_text('wip')
    assert is_dirty(repo.path)
