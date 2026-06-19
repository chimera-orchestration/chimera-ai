from datetime import datetime
from pathlib import Path

from giterator import Git
from giterator.testing import Repo
from testfixtures import TempDir, compare

from chimera.worktrees import (
    base_ref,
    branch,
    default_branch,
    fetch_origin,
    goals,
    is_dirty,
    is_merged,
    registered_worktrees,
    session_name,
    worktree_dirs,
    worktree_path,
)


def _renamed(repo: Repo, to: str) -> Repo:
    repo.commit_content('seed')
    repo('branch', '-m', 'main', to)
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
    compare(worktree_dirs(tmpdir.path), expected=[tmpdir / 'a@agent', tmpdir / 'b@agent'])


def test_worktree_dirs_is_empty_when_root_is_absent(tmpdir: TempDir) -> None:
    compare(worktree_dirs(tmpdir / 'nope'), expected=[])


def test_goals_are_derived_from_agent_worktrees(tmpdir: TempDir) -> None:
    for name in ('g1@agent', 'g2@agent', 'g1@reviewer'):  # reviewer rides g1's agent
        tmpdir.makedir(name)
    compare(goals(tmpdir.path), expected={'g1', 'g2'})


def test_registered_worktrees_lists_repo_and_added(tmpdir: TempDir, git_repo: Repo) -> None:
    git = Git(git_repo.path)
    wt = tmpdir / 'wt'
    git('worktree', 'add', '-b', 'side', str(wt), 'main')
    compare(registered_worktrees(git), expected={git_repo.path.resolve(), wt.resolve()})


def test_is_merged_true_for_ancestor(git_repo: Repo) -> None:
    git = Git(git_repo.path)
    git('branch', 'feature', 'HEAD')  # points at HEAD → an ancestor of it
    assert is_merged(git, 'feature') is True


def test_is_merged_false_for_branch_ahead(git_repo: Repo) -> None:
    git = Git(git_repo.path)
    git('checkout', '-q', '-b', 'feature')
    git_repo.commit_content('ahead')  # feature now ahead of main
    git('checkout', '-q', 'main')
    assert is_merged(git, 'feature') is False


def test_is_dirty(git_repo: Repo) -> None:
    assert is_dirty(git_repo.path) is False
    (git_repo.path / 'scratch.txt').write_text('wip')
    assert is_dirty(git_repo.path) is True


class TestDefaultBranch:
    def test_main(self, git_repo: Repo) -> None:
        compare(default_branch(Git(git_repo.path)), expected='main')

    def test_master_style(self, tmpdir: TempDir) -> None:
        repo = _renamed(Repo.make(tmpdir / 'm'), 'master')
        compare(default_branch(Git(repo.path)), expected='master')

    def test_resolves_via_origin_head(self, tmpdir: TempDir) -> None:
        source = _renamed(Repo.make(tmpdir / 'src'), 'trunk')  # neither main nor master
        compare(default_branch(Git.clone(source.path, tmpdir / 'clone')), expected='trunk')

    def test_falls_back_to_main(self, tmpdir: TempDir) -> None:
        repo = _renamed(Repo.make(tmpdir / 'x'), 'trunk')  # no main/master, no origin
        compare(default_branch(Git(repo.path)), expected='main')


class TestBaseRef:
    def test_ties_favour_local(self, tmpdir: TempDir, git_repo: Repo) -> None:
        compare(base_ref(Git.clone(git_repo.path, tmpdir / 'clone')), expected='main')

    def test_prefers_origin_when_newer(self, tmpdir: TempDir) -> None:
        origin = Repo.make(tmpdir / 'origin')
        origin.commit_content('seed', datetime(2020, 1, 1))
        clone = Git.clone(origin.path, tmpdir / 'clone')
        origin.commit_content('remote-ahead', datetime(2022, 1, 1))
        clone('fetch', 'origin')
        compare(base_ref(clone), expected='origin/main')

    def test_uses_the_default_branch(self, tmpdir: TempDir) -> None:
        repo = _renamed(Repo.make(tmpdir / 'm'), 'master')
        compare(base_ref(Git(repo.path)), expected='master')

    def test_none_when_neither_ref_exists(self, tmpdir: TempDir) -> None:
        repo = _renamed(Repo.make(tmpdir / 'x'), 'trunk')  # default resolves to main, but absent
        compare(base_ref(Git(repo.path)), expected=None)


class TestFetchOrigin:
    def test_no_op_without_an_origin(self, git_repo: Repo) -> None:
        git = Git(git_repo.path)
        fetch_origin(git)  # must not raise when there is no origin remote
        compare(git('remote').split(), expected=[])

    def test_updates_remote_tracking_refs(self, tmpdir: TempDir, git_repo: Repo) -> None:
        clone = Git.clone(git_repo.path, tmpdir / 'clone')
        git_repo.commit_content('remote-ahead')
        fetch_origin(clone)
        compare(
            clone.rev_parse('origin/main', short=False),
            expected=Git(git_repo.path).rev_parse('main', short=False),
        )
