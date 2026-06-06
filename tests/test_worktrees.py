from pathlib import Path

from giterator import Git
from giterator.testing import Repo
from testfixtures import TempDir

from chimera.worktrees import (
    branch,
    goals,
    is_dirty,
    is_merged,
    registered_worktrees,
    worktree_dirs,
    worktree_path,
)


def _seeded_repo(tmpdir: TempDir) -> Repo:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    return repo


def test_branch_names_the_actor_under_the_goal() -> None:
    assert branch('my-goal', 'agent') == 'my-goal/agent'


def test_worktree_path_joins_goal_and_actor_with_a_dash() -> None:
    assert worktree_path(Path('/wt'), 'my-goal', 'agent') == Path('/wt/my-goal-agent')


def test_worktree_dirs_lists_only_dirs_sorted(tmpdir: TempDir) -> None:
    tmpdir.makedir('b-agent')
    tmpdir.makedir('a-agent')
    tmpdir.write('a-file', b'')  # files are ignored
    assert worktree_dirs(tmpdir.path) == [tmpdir.path / 'a-agent', tmpdir.path / 'b-agent']


def test_worktree_dirs_is_empty_when_root_is_absent(tmpdir: TempDir) -> None:
    assert worktree_dirs(tmpdir.path / 'nope') == []


def test_goals_are_derived_from_agent_worktrees(tmpdir: TempDir) -> None:
    for name in ('g1-agent', 'g2-agent', 'g1-reviewer'):  # reviewer rides g1's agent
        tmpdir.makedir(name)
    assert goals(tmpdir.path) == {'g1', 'g2'}


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
