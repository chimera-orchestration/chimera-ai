import pytest
from giterator import Git
from giterator.testing import Repo
from testfixtures import TempDir
from typer.testing import CliRunner

from chimera.__main__ import app
from chimera.commands.goal.new import new

runner = CliRunner()


def _seeded_repo(tmpdir: TempDir) -> Repo:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    return repo


def test_new_creates_two_worktrees(tmpdir: TempDir) -> None:
    repo = _seeded_repo(tmpdir)
    goal_dir = new(repo.path, tmpdir.path / 'worktrees', 'my-goal')
    assert goal_dir == tmpdir.path / 'worktrees' / 'my-goal'
    assert (goal_dir / 'human').is_dir()
    assert (goal_dir / 'agent').is_dir()
    branches = Git(repo.path).branches()
    assert 'my-goal/human' in branches
    assert 'my-goal/agent' in branches


def test_new_checks_out_the_role_branches(tmpdir: TempDir) -> None:
    repo = _seeded_repo(tmpdir)
    goal_dir = new(repo.path, tmpdir.path / 'worktrees', 'g')
    human = Git(goal_dir / 'human')('rev-parse', '--abbrev-ref', 'HEAD').strip()
    agent = Git(goal_dir / 'agent')('rev-parse', '--abbrev-ref', 'HEAD').strip()
    assert human == 'g/human'
    assert agent == 'g/agent'


def test_new_refuses_repo_without_commits(tmpdir: TempDir) -> None:
    repo = Repo.make(tmpdir.path / 'repo')  # no commit → unborn HEAD
    worktrees = tmpdir.path / 'worktrees'
    with pytest.raises(RuntimeError, match='no commits'):
        new(repo.path, worktrees, 'g')
    assert not worktrees.exists()


def test_goal_new_cli(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _seeded_repo(tmpdir)
    project = tmpdir.makedir('project')
    (project / 'config.yaml').write_text(f'repo: {repo.path}\n')
    monkeypatch.chdir(project)
    result = runner.invoke(app, ['goal', 'new', 'feature-x'])
    assert result.exit_code == 0
    assert (project / 'worktrees' / 'feature-x' / 'human').is_dir()
    assert (project / 'worktrees' / 'feature-x' / 'agent').is_dir()
