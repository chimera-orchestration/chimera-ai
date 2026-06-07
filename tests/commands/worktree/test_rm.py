from pathlib import Path

import pytest
from giterator import Git
from giterator.testing import Repo
from testfixtures import TempDir
from typer.testing import CliRunner

from chimera.__main__ import app
from chimera.commands.worktree.add import add
from chimera.commands.worktree.rm import remove

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('chimera.commands.worktree.rm.live_sessions', lambda worktree: [])


def _goal(tmpdir: TempDir) -> tuple[Repo, Path]:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    worktrees = tmpdir.path / 'worktrees'
    add(repo.path, worktrees, 'g')
    return repo, worktrees


def _project(tmpdir: TempDir, repo: Repo, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmpdir.makedir('project')
    (project / 'config.yaml').write_text(f'kind: project\nrepo: {repo.path}\n')
    monkeypatch.chdir(project)
    return project


def test_remove_is_a_noop_for_a_goal_that_was_never_created(tmpdir: TempDir) -> None:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    assert remove(repo.path, tmpdir.path / 'worktrees', 'ghost') == []


def test_remove_aborts_when_an_agent_is_running(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, worktrees = _goal(tmpdir)
    monkeypatch.setattr(
        'chimera.commands.worktree.rm.live_sessions',
        lambda worktree: [{'sessionId': 'x', 'status': 'idle'}],
    )
    with pytest.raises(RuntimeError, match='agent is live'):
        remove(repo.path, worktrees, 'g', force=True)  # not even force bypasses it
    assert (worktrees / 'g@agent').is_dir()  # nothing removed
    assert 'g/human' in Git(repo.path).branches()


def test_remove_takes_out_worktrees_and_branches(tmpdir: TempDir) -> None:
    repo, worktrees = _goal(tmpdir)
    removed = remove(repo.path, worktrees, 'g')
    assert removed == [worktrees / 'g@agent']  # only the agent has a worktree
    assert not (worktrees / 'g@agent').exists()
    branches = Git(repo.path).branches()
    assert 'g/human' not in branches
    assert 'g/agent' not in branches


def test_remove_refuses_uncommitted_changes(tmpdir: TempDir) -> None:
    repo, worktrees = _goal(tmpdir)
    (worktrees / 'g@agent' / 'scratch.txt').write_text('wip')
    with pytest.raises(RuntimeError, match='changes'):
        remove(repo.path, worktrees, 'g')
    assert (worktrees / 'g@agent').is_dir()
    assert 'g/agent' in Git(repo.path).branches()


def test_remove_refuses_unmerged_branch(tmpdir: TempDir) -> None:
    repo, worktrees = _goal(tmpdir)
    Repo(worktrees / 'g@agent').commit_content('work')  # branch now ahead of main
    with pytest.raises(RuntimeError, match='unmerged'):
        remove(repo.path, worktrees, 'g')
    assert (worktrees / 'g@agent').is_dir()
    assert 'g/agent' in Git(repo.path).branches()


def test_remove_force_discards_unsaved_work(tmpdir: TempDir) -> None:
    repo, worktrees = _goal(tmpdir)
    Repo(worktrees / 'g@agent').commit_content('work')  # unmerged
    (worktrees / 'g@agent' / 'scratch.txt').write_text('wip')  # uncommitted
    remove(repo.path, worktrees, 'g', force=True)
    assert not (worktrees / 'g@agent').exists()
    branches = Git(repo.path).branches()
    assert 'g/human' not in branches
    assert 'g/agent' not in branches


def test_worktree_rm_cli(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    project = _project(tmpdir, repo, monkeypatch)
    runner.invoke(app, ['worktree', 'add', 'g'])
    result = runner.invoke(app, ['worktree', 'rm', 'g'])
    assert result.exit_code == 0
    assert not (project / 'worktrees' / 'g@agent').exists()
    assert 'g/human' not in Git(repo.path).branches()


def test_worktree_rm_cli_reports_nothing_to_remove(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    _project(tmpdir, repo, monkeypatch)
    result = runner.invoke(app, ['worktree', 'rm', 'ghost'])
    assert result.exit_code == 0
    assert 'Nothing to remove' in result.output


def test_goal_finish_cli(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    project = _project(tmpdir, repo, monkeypatch)
    runner.invoke(app, ['worktree', 'add', 'g'])
    result = runner.invoke(app, ['goal', 'finish', 'g'])  # finish is the lifecycle name for rm
    assert result.exit_code == 0
    assert not (project / 'worktrees' / 'g@agent').exists()
    assert 'g/agent' not in Git(repo.path).branches()
