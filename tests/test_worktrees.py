from pathlib import Path

from giterator import Git
from giterator.testing import Repo
from testfixtures import TempDir, compare

from chimera.worktrees import (
    branch,
    goals,
    is_dirty,
    is_merged,
    registered_worktrees,
    session_name,
    worktree_dirs,
    worktree_path,
)


def _seeded_repo(tmpdir: TempDir) -> Repo:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    return repo


def test_branch_names_the_actor_under_the_goal() -> None:
    compare(branch('my-goal', 'agent'), expected='my-goal/agent')


def test_worktree_path_joins_goal_and_actor_with_an_at_sign() -> None:
    compare(worktree_path(Path('/wt'), 'my-goal', 'agent'), expected=Path('/wt/my-goal@agent'))


def test_session_name_joins_project_goal_and_actor() -> None:
    compare(session_name('proj', 'my-goal', 'agent'), expected='proj@my-goal@agent')


def test_worktree_dirs_lists_only_dirs_sorted(tmpdir: TempDir) -> None:
    tmpdir.makedir('b@agent')
    tmpdir.makedir('a@agent')
    tmpdir.write('a-file', b'')  # files are ignored
    compare(worktree_dirs(tmpdir.path), expected=[tmpdir.path / 'a@agent', tmpdir.path / 'b@agent'])


def test_worktree_dirs_is_empty_when_root_is_absent(tmpdir: TempDir) -> None:
    compare(worktree_dirs(tmpdir.path / 'nope'), expected=[])


def test_goals_are_derived_from_agent_worktrees(tmpdir: TempDir) -> None:
    for name in ('g1@agent', 'g2@agent', 'g1@reviewer'):  # reviewer rides g1's agent
        tmpdir.makedir(name)
    compare(goals(tmpdir.path), expected={'g1', 'g2'})


def test_registered_worktrees_lists_repo_and_added(tmpdir: TempDir) -> None:
    repo = _seeded_repo(tmpdir)
    git = Git(repo.path)
    wt = tmpdir.path / 'wt'
    git('worktree', 'add', '-b', 'side', str(wt), 'main')
    compare(registered_worktrees(git), expected={repo.path.resolve(), wt.resolve()})


def test_is_merged_true_for_ancestor(tmpdir: TempDir) -> None:
    repo = _seeded_repo(tmpdir)
    git = Git(repo.path)
    git('branch', 'feature', 'HEAD')  # points at HEAD → an ancestor of it
    assert is_merged(git, 'feature') is True


def test_is_merged_false_for_branch_ahead(tmpdir: TempDir) -> None:
    repo = _seeded_repo(tmpdir)
    git = Git(repo.path)
    git('checkout', '-q', '-b', 'feature')
    repo.commit_content('ahead')  # feature now ahead of main
    git('checkout', '-q', 'main')
    assert is_merged(git, 'feature') is False


def test_is_dirty(tmpdir: TempDir) -> None:
    repo = _seeded_repo(tmpdir)
    assert is_dirty(repo.path) is False
    (repo.path / 'scratch.txt').write_text('wip')
    assert is_dirty(repo.path) is True
