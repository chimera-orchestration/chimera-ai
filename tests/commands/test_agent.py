import subprocess
from pathlib import Path

import pytest
from testfixtures import TempDir
from typer.testing import CliRunner

from chimera.__main__ import app
from chimera.commands.agent import agent

runner = CliRunner()


def test_agent_launches_claude_in_the_worktree(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmpdir.makedir('wt')
    calls: list[object] = []
    monkeypatch.setattr(
        subprocess, 'run', lambda cmd, cwd=None, check=False: calls.append((cmd, cwd, check))
    )
    agent(worktree, 'proj-goal')
    assert calls == [(['claude', '--bg', '--name', 'proj-goal'], worktree, True)]


def test_agent_missing_worktree_raises(tmpdir: TempDir) -> None:
    with pytest.raises(FileNotFoundError):
        agent(tmpdir.path / 'nope', 'x')


def test_agent_cli(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmpdir.makedir('myproject')
    worktree = project / 'worktrees' / 'g-agent'
    worktree.mkdir(parents=True)
    calls: list[object] = []
    monkeypatch.setattr(
        subprocess, 'run', lambda cmd, cwd=None, check=False: calls.append((cmd, cwd, check))
    )
    monkeypatch.chdir(project)
    result = runner.invoke(app, ['agent', 'g'])
    assert result.exit_code == 0
    expected = Path.cwd() / 'worktrees' / 'g-agent'  # cwd resolves symlinks like the wrapper
    assert calls == [(['claude', '--bg', '--name', 'myproject-g'], expected, True)]
