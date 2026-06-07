from pathlib import Path

import pytest
from giterator import Git
from giterator.testing import Repo
from testfixtures import TempDir
from typer.testing import CliRunner

from chimera.__main__ import app
from chimera.commands.goal.start import start

runner = CliRunner()


def _seeded_repo(tmpdir: TempDir) -> Repo:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    return repo


def _project(tmpdir: TempDir, repo: Repo, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmpdir.makedir('project')
    (project / 'config.yaml').write_text(f'kind: project\nrepo: {repo.path}\n')
    monkeypatch.chdir(project)
    return project


def test_start_creates_worktrees_then_launches_the_agent(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _seeded_repo(tmpdir)
    worktrees = tmpdir.path / 'worktrees'
    calls: list[object] = []
    monkeypatch.setattr(
        'chimera.commands.goal.start.agent',
        lambda worktree, name, prompt=None: calls.append((worktree, name, prompt)),
    )
    created = start(repo.path, worktrees, 'g', 'proj-g-agent')
    assert created == worktrees / 'g-agent'
    assert (worktrees / 'g-agent').is_dir()
    assert 'g/human' in Git(repo.path).branches()
    assert calls == [(worktrees / 'g-agent', 'proj-g-agent', None)]  # foreground (no prompt)


def test_start_passes_the_prompt_to_the_agent(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _seeded_repo(tmpdir)
    worktrees = tmpdir.path / 'worktrees'
    calls: list[object] = []
    monkeypatch.setattr(
        'chimera.commands.goal.start.agent',
        lambda worktree, name, prompt=None: calls.append((worktree, name, prompt)),
    )
    start(repo.path, worktrees, 'g', 'proj-g-agent', prompt='do it')
    assert calls == [(worktrees / 'g-agent', 'proj-g-agent', 'do it')]


def test_goal_start_cli(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _seeded_repo(tmpdir)
    project = _project(tmpdir, repo, monkeypatch)
    calls: list[object] = []  # stub the agent so real git runs but no claude launches
    monkeypatch.setattr(
        'chimera.commands.goal.start.agent',
        lambda worktree, name, prompt=None: calls.append((worktree, name, prompt)),
    )
    result = runner.invoke(app, ['goal', 'start', 'feature-x'])
    assert result.exit_code == 0
    assert (project / 'worktrees' / 'feature-x-agent').is_dir()
    expected = Path.cwd() / 'worktrees' / 'feature-x-agent'
    assert calls == [(expected, 'project-feature-x-agent', None)]


def test_goal_start_cli_with_prompt(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _seeded_repo(tmpdir)
    _project(tmpdir, repo, monkeypatch)
    calls: list[object] = []
    monkeypatch.setattr(
        'chimera.commands.goal.start.agent',
        lambda worktree, name, prompt=None: calls.append((worktree, name, prompt)),
    )
    result = runner.invoke(app, ['goal', 'start', 'feature-x', 'go build it'])
    assert result.exit_code == 0
    expected = Path.cwd() / 'worktrees' / 'feature-x-agent'
    assert calls == [(expected, 'project-feature-x-agent', 'go build it')]


def test_goal_ls_cli(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _seeded_repo(tmpdir)
    _project(tmpdir, repo, monkeypatch)
    runner.invoke(app, ['worktree', 'add', 'alpha'])
    runner.invoke(app, ['worktree', 'add', 'beta'])
    result = runner.invoke(app, ['goal', 'ls'])
    assert result.exit_code == 0
    assert result.output.split() == ['alpha', 'beta']
