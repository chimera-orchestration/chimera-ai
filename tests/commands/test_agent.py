import subprocess
from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace

import pytest
from testfixtures import TempDir
from typer.testing import CliRunner

from chimera.__main__ import app
from chimera.commands.agent import Agent, agent, agents, all_sessions, last_prompt, live_sessions

runner = CliRunner()


def _stub(monkeypatch: pytest.MonkeyPatch, sessions: Iterable[object] = ()) -> list[object]:
    calls: list[object] = []
    monkeypatch.setattr('chimera.commands.agent.live_sessions', lambda worktree: list(sessions))
    monkeypatch.setattr(
        subprocess, 'run', lambda cmd, cwd=None, check=False: calls.append((cmd, cwd, check))
    )
    return calls


def test_agent_runs_claude_in_the_foreground_by_default(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(monkeypatch)
    agent(worktree, 'proj@goal@agent')
    assert calls == [(['claude', '--name', 'proj@goal@agent'], worktree, True)]


def test_agent_runs_in_the_background_when_given_a_prompt(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(monkeypatch)
    agent(worktree, 'proj@goal@agent', 'fix the bug')
    assert calls == [
        (['claude', '--bg', '--name', 'proj@goal@agent', 'fix the bug'], worktree, True)
    ]


def test_agent_refuses_when_a_session_is_live(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(monkeypatch, sessions=[{'sessionId': 'abc123', 'status': 'idle'}])
    with pytest.raises(RuntimeError, match='already live'):
        agent(worktree, 'proj@goal@agent')
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


def test_all_sessions_queries_claude_unscoped(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        cmd: object, capture_output: bool = False, text: bool = False, check: bool = False
    ):
        captured['cmd'] = cmd
        return SimpleNamespace(stdout='[{"sessionId": "x", "status": "idle"}]')

    monkeypatch.setattr(subprocess, 'run', fake_run)
    assert all_sessions() == [{'sessionId': 'x', 'status': 'idle'}]
    assert captured['cmd'] == ['claude', 'agents', '--json']  # no --cwd → every project


def _project_with_worktree(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmpdir.makedir('myproject')
    (project / 'config.yaml').write_text(f'kind: project\nrepo: {project}\n')
    (project / 'worktrees' / 'g@agent').mkdir(parents=True)
    monkeypatch.chdir(project)
    return project


def test_agent_start_cli(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    _project_with_worktree(tmpdir, monkeypatch)
    calls = _stub(monkeypatch)
    result = runner.invoke(app, ['agent', 'start', '-g', 'g'])
    assert result.exit_code == 0
    expected = Path.cwd() / 'worktrees' / 'g@agent'  # cwd resolves symlinks like the wrapper
    assert calls == [(['claude', '--name', 'myproject@g@agent'], expected, True)]


def test_agent_start_cli_with_prompt(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    _project_with_worktree(tmpdir, monkeypatch)
    calls = _stub(monkeypatch)
    result = runner.invoke(app, ['agent', 'start', '-g', 'g', '--prompt', 'do it'])
    assert result.exit_code == 0
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    assert calls == [(['claude', '--bg', '--name', 'myproject@g@agent', 'do it'], expected, True)]


def test_agent_start_cli_with_actor(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project_with_worktree(tmpdir, monkeypatch)
    (project / 'worktrees' / 'g@reviewer').mkdir()
    calls = _stub(monkeypatch)
    result = runner.invoke(app, ['agent', 'start', '-g', 'g', '-a', 'reviewer'])
    assert result.exit_code == 0
    expected = Path.cwd() / 'worktrees' / 'g@reviewer'
    assert calls == [(['claude', '--name', 'myproject@g@reviewer'], expected, True)]


def test_last_prompt_reads_most_recent_prompt(tmpdir: TempDir) -> None:
    projects = tmpdir.makedir('projects')
    transcript = projects / 'a-project' / 'sess.jsonl'
    transcript.parent.mkdir()
    transcript.write_text(
        '{"type": "user", "message": "hi"}\n'
        '{"type": "last-prompt", "lastPrompt": "fix\\nthe   bug"}\n'
        '\n'  # blank lines are skipped (this one is reached first, in reverse)
        '{"type": "assistant", "message": "ok"}\n'
    )
    assert last_prompt('sess', projects) == 'fix the bug'


def test_last_prompt_when_no_transcript(tmpdir: TempDir) -> None:
    assert last_prompt('missing', tmpdir.path) is None


def test_last_prompt_when_transcript_has_no_prompt(tmpdir: TempDir) -> None:
    projects = tmpdir.makedir('projects')
    transcript = projects / 'p' / 'sess.jsonl'
    transcript.parent.mkdir()
    transcript.write_text('{"type": "user", "message": "hi"}\n')
    assert last_prompt('sess', projects) is None


def test_agents_enriches_sessions_with_name_and_summary(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = tmpdir.makedir('projects')
    transcript = projects / 'p' / 'named.jsonl'
    transcript.parent.mkdir()
    transcript.write_text('{"type": "last-prompt", "lastPrompt": "do the thing"}\n')
    monkeypatch.setattr(
        'chimera.commands.agent.all_sessions',
        lambda: [
            {'sessionId': 'named', 'status': 'busy', 'name': 'proj@goal@agent'},
            {'sessionId': 'bare', 'status': 'idle'},  # no name → falls back to sessionId
        ],
    )
    assert agents(projects) == [
        Agent(name='proj@goal@agent', status='busy', summary='do the thing'),
        Agent(name='bare', status='idle', summary=None),
    ]


def test_agent_ls_cli_lists_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        'chimera.__main__.agents',
        lambda: [
            Agent(name='proj@goal@agent', status='busy', summary='fix the bug'),
            Agent(name='other', status='idle', summary=None),
        ],
    )
    result = runner.invoke(app, ['agent', 'ls'])
    assert result.exit_code == 0
    assert result.output.splitlines() == [
        'proj@goal@agent  busy  fix the bug',
        'other            idle',
    ]


def test_agent_ls_cli_when_nothing_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('chimera.__main__.agents', list)
    result = runner.invoke(app, ['agent', 'ls'])
    assert result.exit_code == 0
    assert 'No agents running' in result.output
