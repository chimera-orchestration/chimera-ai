from datetime import datetime
from pathlib import Path

import pytest
from giterator import Git
from giterator.testing import Repo
from testfixtures import TempDir
from typer.testing import CliRunner

from chimera.__main__ import app
from chimera.commands.worktree.add import add

runner = CliRunner()


def _seeded_repo(tmpdir: TempDir) -> Repo:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    return repo


def _head(path: Path) -> str:
    return Git(path).rev_parse('HEAD', short=False)


def _branch(repo_path: Path, name: str) -> str:
    return Git(repo_path).rev_parse(name, short=False)


def test_add_creates_agent_worktree_and_both_branches(tmpdir: TempDir) -> None:
    repo = _seeded_repo(tmpdir)
    worktrees = tmpdir.path / 'worktrees'
    created = add(repo.path, worktrees, 'my-goal')
    assert created == [worktrees / 'my-goal@agent']  # only the agent gets a worktree
    assert (worktrees / 'my-goal@agent').is_dir()
    assert not (worktrees / 'my-goal@human').exists()  # human branch has no worktree
    branches = Git(repo.path).branches()
    assert 'my-goal/human' in branches
    assert 'my-goal/agent' in branches


def test_add_creates_extra_named_actors(tmpdir: TempDir) -> None:
    repo = _seeded_repo(tmpdir)
    worktrees = tmpdir.path / 'worktrees'
    created = add(repo.path, worktrees, 'g', actors=('human', 'agent', 'reviewer'))
    assert created == [worktrees / 'g@agent', worktrees / 'g@reviewer']
    assert (worktrees / 'g@reviewer').is_dir()
    assert 'g/reviewer' in Git(repo.path).branches()


def test_add_checks_out_the_agent_branch_in_its_worktree(tmpdir: TempDir) -> None:
    repo = _seeded_repo(tmpdir)
    worktrees = tmpdir.path / 'worktrees'
    add(repo.path, worktrees, 'g')
    agent = Git(worktrees / 'g@agent')('rev-parse', '--abbrev-ref', 'HEAD').strip()
    assert agent == 'g/agent'
    assert 'g/human' in Git(repo.path).branches()  # exists, but checked out nowhere


def test_add_branches_from_main_not_checked_out_branch(tmpdir: TempDir) -> None:
    repo = _seeded_repo(tmpdir)
    main = _head(repo.path)
    repo('checkout', '-b', 'feature')
    repo.commit_content('feature-work')
    assert _head(repo.path) != main  # repo is parked on a different commit
    worktrees = tmpdir.path / 'worktrees'
    [created] = add(repo.path, worktrees, 'g')
    assert _head(created) == main
    assert _branch(repo.path, 'g/human') == main


def test_add_branches_from_origin_main_when_newer(tmpdir: TempDir) -> None:
    origin = Repo.make(tmpdir.path / 'origin')
    origin.commit_content('seed', datetime(2020, 1, 1))
    local = Git.clone(origin.path, tmpdir.path / 'repo')
    origin.commit_content('remote-ahead', datetime(2022, 1, 1))
    local('fetch', 'origin')
    expected = local.rev_parse('origin/main', short=False)
    assert expected != local.rev_parse('main', short=False)
    worktrees = tmpdir.path / 'worktrees'
    [created] = add(local.path, worktrees, 'g')
    assert _head(created) == expected
    assert _branch(local.path, 'g/human') == expected


def test_add_branches_from_local_main_when_newer(tmpdir: TempDir) -> None:
    origin = Repo.make(tmpdir.path / 'origin')
    origin.commit_content('seed', datetime(2020, 1, 1))
    local = Git.clone(origin.path, tmpdir.path / 'repo')
    Repo(local.path).commit_content('local-ahead', datetime(2022, 1, 1))
    expected = local.rev_parse('main', short=False)
    assert expected != local.rev_parse('origin/main', short=False)
    worktrees = tmpdir.path / 'worktrees'
    [created] = add(local.path, worktrees, 'g')
    assert _head(created) == expected
    assert _branch(local.path, 'g/human') == expected


def test_add_branches_have_no_upstream_tracking(tmpdir: TempDir) -> None:
    origin = Repo.make(tmpdir.path / 'origin')
    origin.commit_content('seed', datetime(2020, 1, 1))
    local = Git.clone(origin.path, tmpdir.path / 'repo')
    origin.commit_content('remote-ahead', datetime(2022, 1, 1))
    local('fetch', 'origin')  # base resolves to origin/main, a remote-tracking branch
    worktrees = tmpdir.path / 'worktrees'
    add(local.path, worktrees, 'g')
    for ref in ('g/human', 'g/agent'):
        upstream = local('for-each-ref', '--format=%(upstream)', f'refs/heads/{ref}').strip()
        assert upstream == '', f'{ref} should have no upstream, got {upstream!r}'


def test_add_uses_explicit_from_start_point(tmpdir: TempDir) -> None:
    repo = _seeded_repo(tmpdir)
    repo('checkout', '-b', 'release')
    release = repo.commit_content('release-work', short=False)
    repo('checkout', 'main')
    worktrees = tmpdir.path / 'worktrees'
    [created] = add(repo.path, worktrees, 'g', frm='release')
    assert _head(created) == release
    assert _branch(repo.path, 'g/human') == release


def test_add_falls_back_to_head_without_a_main_branch(tmpdir: TempDir) -> None:
    repo = _seeded_repo(tmpdir)
    repo('branch', '-m', 'main', 'trunk')  # no main, no origin/main
    head = _head(repo.path)
    worktrees = tmpdir.path / 'worktrees'
    [created] = add(repo.path, worktrees, 'g')
    assert _head(created) == head
    assert _branch(repo.path, 'g/human') == head


def test_add_refuses_repo_without_commits(tmpdir: TempDir) -> None:
    repo = Repo.make(tmpdir.path / 'repo')  # no commit → unborn HEAD
    worktrees = tmpdir.path / 'worktrees'
    with pytest.raises(RuntimeError, match='no commits'):
        add(repo.path, worktrees, 'g')
    assert not worktrees.exists()


def test_add_refuses_bare_repo_without_commits(tmpdir: TempDir) -> None:
    bare = tmpdir.path / 'bare.git'
    Git(tmpdir.path)('init', '--bare', str(bare))  # no work tree, unborn HEAD
    worktrees = tmpdir.path / 'worktrees'
    with pytest.raises(RuntimeError, match='no commits'):
        add(bare, worktrees, 'g')


def test_worktree_add_cli(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _seeded_repo(tmpdir)
    project = tmpdir.makedir('project')
    (project / 'config.yaml').write_text(f'kind: project\nrepo: {repo.path}\n')
    monkeypatch.chdir(project)
    result = runner.invoke(app, ['worktree', 'add', 'feature-x'])
    assert result.exit_code == 0
    assert (project / 'worktrees' / 'feature-x@agent').is_dir()
    assert not (project / 'worktrees' / 'feature-x@human').exists()
    assert 'feature-x/human' in Git(repo.path).branches()


def test_worktree_add_cli_from_option(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _seeded_repo(tmpdir)
    repo('checkout', '-b', 'release')
    release = repo.commit_content('release-work', short=False)
    repo('checkout', 'main')
    project = tmpdir.makedir('project')
    (project / 'config.yaml').write_text(f'kind: project\nrepo: {repo.path}\n')
    monkeypatch.chdir(project)
    result = runner.invoke(app, ['worktree', 'add', 'feature-x', '--from', 'release'])
    assert result.exit_code == 0
    assert _head(project / 'worktrees' / 'feature-x@agent') == release


def test_worktree_ls_cli(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _seeded_repo(tmpdir)
    project = tmpdir.makedir('project')
    (project / 'config.yaml').write_text(f'kind: project\nrepo: {repo.path}\n')
    monkeypatch.chdir(project)
    runner.invoke(app, ['worktree', 'add', 'g'])
    result = runner.invoke(app, ['worktree', 'ls'])
    assert result.exit_code == 0
    assert str(project / 'worktrees' / 'g@agent') in result.output
