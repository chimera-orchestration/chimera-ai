import subprocess
from pathlib import Path

import pytest
from testfixtures import TempDir
from typer.testing import CliRunner

from chimera.__main__ import app
from chimera.commands.agent import agent

runner = CliRunner()


def _record(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    calls: list[object] = []
    monkeypatch.setattr(
        subprocess, 'run', lambda cmd, cwd=None, check=False: calls.append((cmd, cwd, check))
    )
    return calls


def test_agent_launches_claude_in_the_worktree(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _record(monkeypatch)
    agent(worktree, 'proj-goal')
    assert calls == [(['claude', '--bg', '--name', 'proj-goal'], worktree, True)]


def test_agent_appends_prompt(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _record(monkeypatch)
    agent(worktree, 'proj-goal', 'fix the bug')
    assert calls == [(['claude', '--bg', '--name', 'proj-goal', 'fix the bug'], worktree, True)]


def test_agent_missing_worktree_raises(tmpdir: TempDir) -> None:
    with pytest.raises(FileNotFoundError):
        agent(tmpdir.path / 'nope', 'x')


def test_agent_cli(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmpdir.makedir('myproject')
    worktree = project / 'worktrees' / 'g-agent'
    worktree.mkdir(parents=True)
    calls = _record(monkeypatch)
    monkeypatch.chdir(project)
    result = runner.invoke(app, ['agent', 'g'])
    assert result.exit_code == 0
    expected = Path.cwd() / 'worktrees' / 'g-agent'  # cwd resolves symlinks like the wrapper
    assert calls == [(['claude', '--bg', '--name', 'myproject-g'], expected, True)]


def test_agent_cli_with_prompt(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmpdir.makedir('myproject')
    worktree = project / 'worktrees' / 'g-agent'
    worktree.mkdir(parents=True)
    calls = _record(monkeypatch)
    monkeypatch.chdir(project)
    result = runner.invoke(app, ['agent', 'g', 'do it'])
    assert result.exit_code == 0
    expected = Path.cwd() / 'worktrees' / 'g-agent'
    assert calls == [(['claude', '--bg', '--name', 'myproject-g', 'do it'], expected, True)]
