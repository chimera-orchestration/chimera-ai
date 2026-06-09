import os
import subprocess
from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace

import pytest
from testfixtures import TempDir
from typer.testing import CliRunner

from chimera.__main__ import app
from chimera.commands.agent import (
    Agent,
    agent,
    agents,
    all_sessions,
    live_sessions,
    session_summary,
)

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
    result = runner.invoke(app, ['agent', 'start', 'do it', '-g', 'g'])
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


def _transcript(folder: Path, name: str, body: str, mtime: float) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / name
    f.write_text(body)
    os.utime(f, (mtime, mtime))
    return f


def test_session_summary_reads_newest_transcript_for_cwd(tmpdir: TempDir) -> None:
    projects = tmpdir.makedir('projects')
    folder = projects / '-work-proj'  # munged from the cwd below
    _transcript(folder, 'old.jsonl', '{"type": "last-prompt", "lastPrompt": "stale"}\n', 1000)
    _transcript(
        folder,
        'live.jsonl',
        '{"type": "user", "message": "hi"}\n'
        '{"type": "last-prompt", "lastPrompt": "fix\\nthe   bug"}\n'
        '\n'  # blank lines are skipped (this one is reached first, in reverse)
        '{"type": "assistant", "message": "ok"}\n',
        2000,
    )
    assert session_summary('/work/proj', 'agent', projects) == 'fix the bug'


def test_session_summary_prefers_title_over_prompt(tmpdir: TempDir) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(
        projects / '-work-proj',
        's.jsonl',
        '{"type": "last-prompt", "lastPrompt": "fix the bug"}\n'
        '{"type": "ai-title", "aiTitle": "ai topic"}\n'
        '{"type": "custom-title", "customTitle": "my title"}\n',
        1000,
    )
    assert session_summary('/work/proj', 'agent', projects) == 'my title'


def test_session_summary_uses_ai_title_when_no_custom_title(tmpdir: TempDir) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(
        projects / '-work-proj',
        's.jsonl',
        '{"type": "last-prompt", "lastPrompt": "fix the bug"}\n'
        '{"type": "ai-title", "aiTitle": "ai topic"}\n',
        1000,
    )
    assert session_summary('/work/proj', 'agent', projects) == 'ai topic'


def test_session_summary_skips_title_equal_to_name(tmpdir: TempDir) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(
        projects / '-work-proj',
        's.jsonl',
        # Claude persists --name as a custom-title; it must not just echo the name.
        '{"type": "custom-title", "customTitle": "proj@goal@agent"}\n'
        '{"type": "last-prompt", "lastPrompt": "fix the bug"}\n',
        1000,
    )
    assert session_summary('/work/proj', 'proj@goal@agent', projects) == 'fix the bug'


def test_session_summary_takes_latest_of_each_record(tmpdir: TempDir) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(
        projects / '-work-proj',
        's.jsonl',
        '{"type": "custom-title", "customTitle": "old name"}\n'
        '{"type": "custom-title", "customTitle": "new name"}\n',
        1000,
    )
    assert session_summary('/work/proj', 'agent', projects) == 'new name'


def test_session_summary_skips_typed_record_missing_its_value(tmpdir: TempDir) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(
        projects / '-work-proj',
        's.jsonl',
        # a last-prompt record may carry no lastPrompt field; fall through to what does
        '{"type": "last-prompt"}\n{"type": "ai-title", "aiTitle": "ai topic"}\n',
        1000,
    )
    assert session_summary('/work/proj', 'agent', projects) == 'ai topic'


def test_session_summary_when_no_folder(tmpdir: TempDir) -> None:
    assert session_summary('/work/proj', 'agent', tmpdir.path) is None


def test_session_summary_when_transcript_has_no_title_or_prompt(tmpdir: TempDir) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(projects / '-work-proj', 'sess.jsonl', '{"type": "user", "message": "hi"}\n', 1000)
    assert session_summary('/work/proj', 'agent', projects) is None


def test_agents_enriches_sessions_with_name_cwd_and_summary(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(
        projects / '-work-proj', 'a.jsonl', '{"type": "last-prompt", "lastPrompt": "do it"}\n', 1000
    )
    monkeypatch.setattr(
        'chimera.commands.agent.all_sessions',
        lambda: [
            {'sessionId': 'x', 'status': 'busy', 'name': 'proj@goal@agent', 'cwd': '/work/proj'},
            {'sessionId': 'bare', 'status': 'idle', 'cwd': '/elsewhere'},  # no name, no transcript
        ],
    )
    assert agents(projects) == [
        Agent(name='proj@goal@agent', status='busy', cwd=Path('/work/proj'), summary='do it'),
        Agent(name='bare', status='idle', cwd=Path('/elsewhere'), summary=None),
    ]


def test_agents_tolerates_sessions_missing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        'chimera.commands.agent.all_sessions',
        # a session without status/cwd (e.g. a foreground session) must not crash the listing;
        # status falls back to state, then '?', and a missing cwd yields no summary
        lambda: [{'sessionId': 'lonely', 'state': 'working'}],
    )
    assert agents() == [Agent(name='lonely', status='working', cwd=Path('.'), summary=None)]


def test_agent_detail_falls_back_to_tilde_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: Path('/home/me')))
    assert Agent('n', 'idle', Path('/home/me/work'), 'a prompt').detail == 'a prompt'
    assert Agent('n', 'idle', Path('/home/me/work'), None).detail == '~/work'
    assert Agent('n', 'idle', Path('/other'), None).detail == '/other'


def test_agent_ls_cli_lists_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        'chimera.__main__.agents',
        lambda: [
            Agent(name='proj@goal@agent', status='busy', cwd=Path('/x'), summary='fix the bug'),
            Agent(name='other', status='idle', cwd=Path('/srv/thing'), summary=None),
        ],
    )
    result = runner.invoke(app, ['agent', 'ls'])
    assert result.exit_code == 0
    assert result.output.splitlines() == [
        'proj@goal@agent  busy  fix the bug',
        'other            idle  /srv/thing',
    ]


def test_agent_ls_cli_when_nothing_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('chimera.__main__.agents', list)
    result = runner.invoke(app, ['agent', 'ls'])
    assert result.exit_code == 0
    assert 'No agents running' in result.output
