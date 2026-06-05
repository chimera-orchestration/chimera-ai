from pathlib import Path

import pytest
from giterator import Git
from giterator.testing import Repo
from testfixtures import TempDir
from typer.testing import CliRunner

from chimera.__main__ import app
from chimera.commands.goal.new import new
from chimera.commands.project.forget import forget

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('chimera.commands.goal.cleanup.live_sessions', lambda worktree: [])


def _project(tmpdir: TempDir, *, with_goal: bool = False) -> tuple[Path, Repo, Path]:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    workspace = tmpdir.makedir('lycia')
    project = workspace / 'myproj'
    project.mkdir()
    (project / 'config.yaml').write_text(f'repo: {repo.path}\n')
    if with_goal:
        new(repo.path, project / 'worktrees', 'g')
    return workspace, repo, project


def test_forget_is_a_noop_when_the_project_is_absent(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    assert forget(workspace, 'ghost') is None


def test_forget_refuses_a_dir_that_is_not_a_tracked_project(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    stray = workspace / 'stray'
    stray.mkdir()
    with pytest.raises(RuntimeError, match='not a tracked project'):
        forget(workspace, 'stray')
    assert stray.is_dir()


def test_forget_removes_a_project_with_no_goals(tmpdir: TempDir) -> None:
    workspace, repo, project = _project(tmpdir)
    assert forget(workspace, 'myproj') == project
    assert not project.exists()
    assert repo.path.is_dir()  # the external tracked repo is left untouched


def test_forget_refuses_while_goals_exist(tmpdir: TempDir) -> None:
    workspace, repo, project = _project(tmpdir, with_goal=True)
    with pytest.raises(RuntimeError, match='still has goals'):
        forget(workspace, 'myproj')
    assert (project / 'worktrees' / 'g-agent').is_dir()
    assert 'g/agent' in Git(repo.path).branches()


def test_forget_force_cleans_goals_then_removes_the_project(tmpdir: TempDir) -> None:
    workspace, repo, project = _project(tmpdir, with_goal=True)
    Repo(project / 'worktrees' / 'g-agent').commit_content('work')  # unmerged
    (project / 'worktrees' / 'g-agent' / 'scratch.txt').write_text('wip')  # uncommitted
    assert forget(workspace, 'myproj', force=True) == project
    assert not project.exists()
    branches = Git(repo.path).branches()
    assert 'g/agent' not in branches
    assert 'g/human' not in branches


def test_forget_force_aborts_when_an_agent_is_running(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, repo, project = _project(tmpdir, with_goal=True)
    monkeypatch.setattr(
        'chimera.commands.goal.cleanup.live_sessions',
        lambda worktree: [{'sessionId': 'x', 'status': 'idle'}],
    )
    with pytest.raises(RuntimeError, match='agent is live'):
        forget(workspace, 'myproj', force=True)  # not even force nukes a live agent
    assert (project / 'worktrees' / 'g-agent').is_dir()


def test_forget_cli(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, repo, project = _project(tmpdir)
    monkeypatch.chdir(workspace)
    result = runner.invoke(app, ['project', 'forget', 'myproj'])
    assert result.exit_code == 0
    assert 'Forgot' in result.output
    assert not project.exists()


def test_forget_cli_reports_nothing_to_forget(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmpdir.makedir('lycia')
    monkeypatch.chdir(workspace)
    result = runner.invoke(app, ['project', 'forget', 'ghost'])
    assert result.exit_code == 0
    assert 'No project named ghost' in result.output
