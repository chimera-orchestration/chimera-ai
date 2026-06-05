import subprocess
from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace

import pytest
from testfixtures import TempDir
from typer.testing import CliRunner

from chimera.__main__ import app
from chimera.commands.agent import agent, live_sessions

runner = CliRunner()


def _stub(monkeypatch: pytest.MonkeyPatch, sessions: Iterable[object] = ()) -> list[object]:
    calls: list[object] = []
    monkeypatch.setattr('chimera.commands.agent.live_sessions', lambda worktree: list(sessions))
    monkeypatch.setattr(
        subprocess, 'run', lambda cmd, cwd=None, check=False: calls.append((cmd, cwd, check))
    )
    return calls


def test_agent_launches_claude_in_the_worktree(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(monkeypatch)
    agent(worktree, 'proj-goal')
    assert calls == [(['claude', '--bg', '--name', 'proj-goal'], worktree, True)]


def test_agent_appends_prompt(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(monkeypatch)
    agent(worktree, 'proj-goal', 'fix the bug')
    assert calls == [(['claude', '--bg', '--name', 'proj-goal', 'fix the bug'], worktree, True)]


def test_agent_refuses_when_a_session_is_live(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(monkeypatch, sessions=[{'sessionId': 'abc123', 'status': 'idle'}])
    with pytest.raises(RuntimeError, match='already live'):
        agent(worktree, 'proj-goal')
    assert calls == []  # never launched


def test_agent_missing_worktree_raises(tmpdir: TempDir) -> None:
    with pytest.raises(FileNotFoundError):
        agent(tmpdir.path / 'nope', 'x')


def test_live_sessions_queries_claude_by_cwd(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmpdir.makedir('wt')
    captured: dict[str, object] = {}

    def fake_run(
        cmd: object, capture_output: bool = False, text: bool = False, check: bool = False
    ):
        captured['cmd'] = cmd
        return SimpleNamespace(stdout='[{"sessionId": "x", "status": "idle"}]')

    monkeypatch.setattr(subprocess, 'run', fake_run)
    sessions = live_sessions(worktree)
    assert captured['cmd'] == ['claude', 'agents', '--json', '--cwd', str(worktree)]
    assert sessions == [{'sessionId': 'x', 'status': 'idle'}]


def test_agent_cli(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmpdir.makedir('myproject')
    (project / 'config.yaml').write_text(f'kind: project\nrepo: {project}\n')
    worktree = project / 'worktrees' / 'g-agent'
    worktree.mkdir(parents=True)
    calls = _stub(monkeypatch)
    monkeypatch.chdir(project)
    result = runner.invoke(app, ['agent', 'g'])
    assert result.exit_code == 0
    expected = Path.cwd() / 'worktrees' / 'g-agent'  # cwd resolves symlinks like the wrapper
    assert calls == [(['claude', '--bg', '--name', 'myproject-g'], expected, True)]


def test_agent_cli_with_prompt(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmpdir.makedir('myproject')
    (project / 'config.yaml').write_text(f'kind: project\nrepo: {project}\n')
    worktree = project / 'worktrees' / 'g-agent'
    worktree.mkdir(parents=True)
    calls = _stub(monkeypatch)
    monkeypatch.chdir(project)
    result = runner.invoke(app, ['agent', 'g', 'do it'])
    assert result.exit_code == 0
    expected = Path.cwd() / 'worktrees' / 'g-agent'
    assert calls == [(['claude', '--bg', '--name', 'myproject-g', 'do it'], expected, True)]
