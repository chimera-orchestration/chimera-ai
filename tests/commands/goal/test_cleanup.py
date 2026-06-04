from pathlib import Path

import pytest
from giterator import Git
from giterator.testing import Repo
from testfixtures import TempDir
from typer.testing import CliRunner

from chimera.__main__ import app
from chimera.commands.goal.cleanup import cleanup
from chimera.commands.goal.new import new

runner = CliRunner()


def _goal(tmpdir: TempDir) -> tuple[Repo, Path]:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    worktrees = tmpdir.path / 'worktrees'
    new(repo.path, worktrees, 'g')
    return repo, worktrees


def test_cleanup_removes_worktrees_and_branches(tmpdir: TempDir) -> None:
    repo, worktrees = _goal(tmpdir)
    removed = cleanup(repo.path, worktrees, 'g')
    assert removed == [worktrees / 'g-human', worktrees / 'g-agent']
    assert not (worktrees / 'g-human').exists()
    assert not (worktrees / 'g-agent').exists()
    branches = Git(repo.path).branches()
    assert 'g/human' not in branches
    assert 'g/agent' not in branches


def test_cleanup_refuses_uncommitted_changes(tmpdir: TempDir) -> None:
    repo, worktrees = _goal(tmpdir)
    (worktrees / 'g-human' / 'scratch.txt').write_text('wip')
    with pytest.raises(RuntimeError, match='changes'):
        cleanup(repo.path, worktrees, 'g')
    assert (worktrees / 'g-human').is_dir()
    assert 'g/human' in Git(repo.path).branches()


def test_cleanup_refuses_unmerged_branch(tmpdir: TempDir) -> None:
    repo, worktrees = _goal(tmpdir)
    Repo(worktrees / 'g-human').commit_content('work')  # branch now ahead of main
    with pytest.raises(RuntimeError, match='unmerged'):
        cleanup(repo.path, worktrees, 'g')
    assert (worktrees / 'g-human').is_dir()
    assert 'g/human' in Git(repo.path).branches()


def test_cleanup_force_discards_unsaved_work(tmpdir: TempDir) -> None:
    repo, worktrees = _goal(tmpdir)
    Repo(worktrees / 'g-human').commit_content('work')  # unmerged
    (worktrees / 'g-agent' / 'scratch.txt').write_text('wip')  # uncommitted
    cleanup(repo.path, worktrees, 'g', force=True)
    assert not (worktrees / 'g-human').exists()
    assert not (worktrees / 'g-agent').exists()
    branches = Git(repo.path).branches()
    assert 'g/human' not in branches
    assert 'g/agent' not in branches


def test_goal_cleanup_cli(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    project = tmpdir.makedir('project')
    (project / 'config.yaml').write_text(f'repo: {repo.path}\n')
    monkeypatch.chdir(project)
    runner.invoke(app, ['goal', 'new', 'g'])
    result = runner.invoke(app, ['goal', 'cleanup', 'g'])
    assert result.exit_code == 0
    assert not (project / 'worktrees' / 'g-human').exists()
    assert 'g/human' not in Git(repo.path).branches()
