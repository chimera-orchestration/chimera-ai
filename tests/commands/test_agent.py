import os
import subprocess
from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace

import pytest
from testfixtures import Replacer, TempDir
from typer.testing import CliRunner

from chimera import __main__ as chimera_main
from chimera.__main__ import app
from chimera.commands.agent import (
    Agent,
    agent,
    agents,
    all_sessions,
    in_goal,
    live_sessions,
    resume,
    scoped,
    session_summary,
    under,
)
from chimera.config import ProjectConfig
from chimera.context import Project, Scope

runner = CliRunner()


def _project_obj(directory: Path) -> Project:
    return Project(directory, ProjectConfig(kind='project', repo=Path('/r')))


def _agent_at(cwd: Path, name: str = 'a') -> Agent:
    return Agent(name, name, 'idle', cwd, None)


def _stub(replace: Replacer, sessions: Iterable[object] = ()) -> list[object]:
    calls: list[object] = []
    replace.in_module(live_sessions, lambda worktree: list(sessions))
    replace.in_module(
        subprocess.run, lambda cmd, cwd=None, check=False: calls.append((cmd, cwd, check))
    )
    return calls


def test_agent_runs_claude_in_the_foreground_by_default(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    agent(worktree, 'proj@goal@agent')
    assert calls == [(['claude', '--name', 'proj@goal@agent'], worktree, True)]


def test_agent_runs_in_the_background_when_given_a_prompt(
    tmpdir: TempDir, replace: Replacer
) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    agent(worktree, 'proj@goal@agent', 'fix the bug')
    assert calls == [
        (['claude', '--bg', '--name', 'proj@goal@agent', 'fix the bug'], worktree, True)
    ]


def test_agent_refuses_when_a_session_is_live(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace, sessions=[{'sessionId': 'abc123', 'status': 'idle'}])
    with pytest.raises(RuntimeError, match='already live'):
        agent(worktree, 'proj@goal@agent')
    assert calls == []  # never launched


def test_agent_missing_worktree_raises(tmpdir: TempDir) -> None:
    with pytest.raises(FileNotFoundError):
        agent(tmpdir.path / 'nope', 'x')


def test_agent_passes_extra_flags_through(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    agent(worktree, 'proj@goal@agent', extra=['--model', 'opus'])
    expected = ['claude', '--name', 'proj@goal@agent', '--model', 'opus']
    assert calls == [(expected, worktree, True)]


def test_resume_runs_claude_resume_in_the_foreground_by_default(
    tmpdir: TempDir, replace: Replacer
) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    resume(worktree, 'proj@goal@agent')
    assert calls == [(['claude', '--resume', 'proj@goal@agent'], worktree, True)]


def test_resume_runs_in_the_background_when_given_a_prompt(
    tmpdir: TempDir, replace: Replacer
) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    resume(worktree, 'proj@goal@agent', 'carry on')
    assert calls == [
        (['claude', '--bg', '--resume', 'proj@goal@agent', 'carry on'], worktree, True)
    ]


def test_resume_passes_extra_flags_through(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    resume(worktree, 'proj@goal@agent', extra=['--dangerously-skip-permissions'])
    expected = ['claude', '--resume', 'proj@goal@agent', '--dangerously-skip-permissions']
    assert calls == [(expected, worktree, True)]


def test_resume_refuses_when_a_session_is_live(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace, sessions=[{'sessionId': 'abc123', 'status': 'idle'}])
    with pytest.raises(RuntimeError, match='already live'):
        resume(worktree, 'proj@goal@agent')
    assert calls == []  # never launched


def test_resume_missing_worktree_raises(tmpdir: TempDir) -> None:
    with pytest.raises(FileNotFoundError):
        resume(tmpdir.path / 'nope', 'x')


def test_live_sessions_queries_claude_by_cwd(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    captured: dict[str, object] = {}

    def fake_run(
        cmd: object, capture_output: bool = False, text: bool = False, check: bool = False
    ):
        captured['cmd'] = cmd
        return SimpleNamespace(stdout='[{"sessionId": "x", "status": "idle"}]')

    replace.in_module(subprocess.run, fake_run)
    sessions = live_sessions(worktree)
    assert captured['cmd'] == ['claude', 'agents', '--json', '--cwd', str(worktree)]
    assert sessions == [{'sessionId': 'x', 'status': 'idle'}]


def test_all_sessions_queries_claude_unscoped(replace: Replacer) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        cmd: object, capture_output: bool = False, text: bool = False, check: bool = False
    ):
        captured['cmd'] = cmd
        return SimpleNamespace(stdout='[{"sessionId": "x", "status": "idle"}]')

    replace.in_module(subprocess.run, fake_run)
    assert all_sessions() == [{'sessionId': 'x', 'status': 'idle'}]
    assert captured['cmd'] == ['claude', 'agents', '--json']  # no --cwd → every project


def _project_with_worktree(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmpdir.makedir('myproject')
    (project / 'config.yaml').write_text(f'kind: project\nrepo: {project}\n')
    (project / 'worktrees' / 'g@agent').mkdir(parents=True)
    monkeypatch.chdir(project)
    return project


def test_agent_start_cli(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch, replace: Replacer
) -> None:
    _project_with_worktree(tmpdir, monkeypatch)
    calls = _stub(replace)
    result = runner.invoke(app, ['agent', 'start', '-g', 'g'])
    assert result.exit_code == 0
    expected = Path.cwd() / 'worktrees' / 'g@agent'  # cwd resolves symlinks like the wrapper
    assert calls == [(['claude', '--name', 'myproject@g@agent'], expected, True)]


def test_agent_start_cli_with_prompt(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch, replace: Replacer
) -> None:
    _project_with_worktree(tmpdir, monkeypatch)
    calls = _stub(replace)
    result = runner.invoke(app, ['agent', 'start', 'do it', '-g', 'g'])
    assert result.exit_code == 0
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    assert calls == [(['claude', '--bg', '--name', 'myproject@g@agent', 'do it'], expected, True)]


def test_agent_start_cli_with_actor(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch, replace: Replacer
) -> None:
    project = _project_with_worktree(tmpdir, monkeypatch)
    (project / 'worktrees' / 'g@reviewer').mkdir()
    calls = _stub(replace)
    result = runner.invoke(app, ['agent', 'start', '-g', 'g', '-a', 'reviewer'])
    assert result.exit_code == 0
    expected = Path.cwd() / 'worktrees' / 'g@reviewer'
    assert calls == [(['claude', '--name', 'myproject@g@reviewer'], expected, True)]


def test_agent_start_cli_forwards_flags_after_dashdash(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch, replace: Replacer
) -> None:
    _project_with_worktree(tmpdir, monkeypatch)
    calls = _stub(replace)
    # no prompt, only passthrough: the flag must not be mistaken for the prompt
    result = runner.invoke(
        app, ['agent', 'start', '-g', 'g', '--', '--dangerously-skip-permissions']
    )
    assert result.exit_code == 0
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    command = ['claude', '--name', 'myproject@g@agent', '--dangerously-skip-permissions']
    assert calls == [(command, expected, True)]


def test_agent_start_cli_with_prompt_and_passthrough(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch, replace: Replacer
) -> None:
    _project_with_worktree(tmpdir, monkeypatch)
    calls = _stub(replace)
    result = runner.invoke(app, ['agent', 'start', 'do it', '-g', 'g', '--', '--model', 'opus'])
    assert result.exit_code == 0
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    command = ['claude', '--bg', '--name', 'myproject@g@agent', '--model', 'opus', 'do it']
    assert calls == [(command, expected, True)]


def test_agent_resume_cli(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch, replace: Replacer
) -> None:
    _project_with_worktree(tmpdir, monkeypatch)
    calls = _stub(replace)
    result = runner.invoke(app, ['agent', 'resume', '-g', 'g'])
    assert result.exit_code == 0
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    assert calls == [(['claude', '--resume', 'myproject@g@agent'], expected, True)]


def test_agent_resume_cli_with_passthrough(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch, replace: Replacer
) -> None:
    _project_with_worktree(tmpdir, monkeypatch)
    calls = _stub(replace)
    result = runner.invoke(
        app, ['agent', 'resume', '-g', 'g', '--', '--dangerously-skip-permissions']
    )
    assert result.exit_code == 0
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    command = ['claude', '--resume', 'myproject@g@agent', '--dangerously-skip-permissions']
    assert calls == [(command, expected, True)]


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
    tmpdir: TempDir, replace: Replacer
) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(
        projects / '-work-proj', 'a.jsonl', '{"type": "last-prompt", "lastPrompt": "do it"}\n', 1000
    )
    replace.in_module(
        all_sessions,
        lambda: [
            {'id': 'x', 'status': 'busy', 'name': 'proj@goal@agent', 'cwd': '/work/proj'},
            {'sessionId': 'bare', 'status': 'idle', 'cwd': '/elsewhere'},  # no name, no transcript
        ],
    )
    assert agents(projects) == [
        Agent(
            id='x', name='proj@goal@agent', status='busy', cwd=Path('/work/proj'), summary='do it'
        ),
        Agent(id='bare', name='bare', status='idle', cwd=Path('/elsewhere'), summary=None),
    ]


def test_agents_tolerates_sessions_missing_fields(replace: Replacer) -> None:
    replace.in_module(
        all_sessions,
        # a session without status/cwd (e.g. a foreground session) must not crash the listing;
        # status falls back to state, then '?', and a missing cwd yields no summary
        lambda: [{'sessionId': 'lonely', 'state': 'working'}],
    )
    assert agents() == [
        Agent(id='lonely', name='lonely', status='working', cwd=Path('.'), summary=None)
    ]


def test_agent_detail_falls_back_to_tilde_cwd(replace: Replacer) -> None:
    replace.on_class(Path.home, lambda cls: Path('/home/me'))
    assert Agent('i', 'n', 'idle', Path('/home/me/work'), 'a prompt').detail == 'a prompt'
    assert Agent('i', 'n', 'idle', Path('/home/me/work'), None).detail == '~/work'
    assert Agent('i', 'n', 'idle', Path('/other'), None).detail == '/other'


def test_scoped_unpinned_keeps_every_agent_when_otherwise_is_none(tmpdir: TempDir) -> None:
    ws = tmpdir.makedir('lycia')
    inside = _agent_at(ws / 'proj' / 'worktrees' / 'g@agent', 'inside')
    outside = _agent_at(tmpdir.path / 'elsewhere', 'outside')
    assert scoped([inside, outside], Scope(ws, None, None), otherwise=None) == [inside, outside]


def test_scoped_unpinned_bounds_to_otherwise_when_given(tmpdir: TempDir) -> None:
    ws = tmpdir.makedir('lycia')
    inside = _agent_at(ws / 'proj' / 'worktrees' / 'g@agent', 'inside')
    outside = _agent_at(tmpdir.path / 'elsewhere', 'outside')
    assert scoped([inside, outside], Scope(ws, None, None), otherwise=ws) == [inside]


def test_scoped_project_keeps_only_agents_under_the_project(tmpdir: TempDir) -> None:
    ws = tmpdir.makedir('lycia')
    project = _project_obj(ws / 'proj')
    inside = _agent_at(ws / 'proj' / 'worktrees' / 'g@agent', 'inside')
    other = _agent_at(ws / 'q' / 'worktrees' / 'g@agent', 'other')
    assert scoped([inside, other], Scope(ws, project, None), otherwise=None) == [inside]


def test_scoped_goal_matches_every_actor_worktree_only(tmpdir: TempDir) -> None:
    ws = tmpdir.makedir('lycia')
    project = _project_obj(ws / 'proj')
    worktrees = ws / 'proj' / 'worktrees'
    agent_wt = _agent_at(worktrees / 'g@agent', 'agent')
    reviewer_sub = _agent_at(worktrees / 'g@reviewer' / 'src', 'reviewer')  # a subdir still counts
    other_goal = _agent_at(worktrees / 'gg@agent', 'other-goal')  # 'gg' must not match 'g'
    in_repo = _agent_at(ws / 'proj' / 'repo', 'repo')  # in the project, not a goal worktree
    listing = [agent_wt, reviewer_sub, other_goal, in_repo]
    assert scoped(listing, Scope(ws, project, 'g'), otherwise=None) == [agent_wt, reviewer_sub]


def test_under_and_in_goal(tmpdir: TempDir) -> None:
    root = tmpdir.makedir('r')
    assert under(root, root) and under(root / 'a' / 'b', root)
    assert not under(tmpdir.path / 'other', root)
    worktrees = tmpdir.makedir('wt')
    assert in_goal(worktrees / 'g@agent', worktrees, 'g')
    assert not in_goal(worktrees / 'goal@agent', worktrees, 'g')  # boundary: 'g' ≠ 'goal'
    assert not in_goal(worktrees, worktrees, 'g')  # the worktrees dir itself is not in a goal


def _scoped_cli(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch, replace: Replacer) -> Path:
    ws = tmpdir.makedir('lycia')
    (ws / 'config.yaml').write_text('kind: workspace\n')
    project = ws / 'proj'
    (project / 'worktrees' / 'g@agent').mkdir(parents=True)
    (project / 'config.yaml').write_text(f'kind: project\nrepo: {project}\n')
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    monkeypatch.chdir(ws)
    return project


def test_agent_ls_cli_unpinned_lists_every_agent(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch, replace: Replacer
) -> None:
    project = _scoped_cli(tmpdir, monkeypatch, replace)
    worktree = project / 'worktrees' / 'g@agent'
    replace.in_module(
        agents,
        lambda: [
            Agent(
                id='aaa11111', name='proj@g@agent', status='busy', cwd=worktree, summary='fix it'
            ),
            Agent(id='bbb22222', name='other', status='idle', cwd=worktree, summary='do a thing'),
            Agent(id='ccc', name='ccc', status='idle', cwd=worktree, summary='unnamed'),
            Agent(id='ddd', name='stray', status='idle', cwd=tmpdir.path / 'outside', summary='x'),
        ],
        module=chimera_main,
    )
    result = runner.invoke(app, ['agent', 'ls'])
    assert result.exit_code == 0
    assert result.output.splitlines() == [  # unpinned → every agent, even the outside stray
        'aaa11111  proj@g@agent  busy  fix it',
        'bbb22222  other         idle  do a thing',
        'ccc                     idle  unnamed',  # name blanked: it merely echoes the id
        'ddd       stray         idle  x',
    ]


def test_agent_ls_cli_trims_long_detail(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch, replace: Replacer
) -> None:
    project = _scoped_cli(tmpdir, monkeypatch, replace)
    worktree = project / 'worktrees' / 'g@agent'
    detail = 'x' * 200
    replace.in_module(
        agents,
        lambda: [Agent(id='aaa', name='named', status='busy', cwd=worktree, summary=detail)],
        module=chimera_main,
    )
    result = runner.invoke(app, ['agent', 'ls'])
    assert result.exit_code == 0
    assert result.output.splitlines() == ['aaa  named  busy  ' + 'x' * 79 + '…']


def test_agent_ls_cli_pinned_to_project_filters_strays(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch, replace: Replacer
) -> None:
    project = _scoped_cli(tmpdir, monkeypatch, replace)
    worktree = project / 'worktrees' / 'g@agent'
    replace.in_module(
        agents,
        lambda: [
            Agent(id='aaa', name='proj@g@agent', status='busy', cwd=worktree, summary='fix it'),
            Agent(id='ddd', name='stray', status='idle', cwd=tmpdir.path / 'outside', summary='x'),
        ],
        module=chimera_main,
    )
    result = runner.invoke(app, ['agent', 'ls', '-p', 'proj'])
    assert result.exit_code == 0
    assert result.output.splitlines() == ['aaa  proj@g@agent  busy  fix it']


def test_agent_ls_cli_when_nothing_running(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch, replace: Replacer
) -> None:
    _scoped_cli(tmpdir, monkeypatch, replace)
    replace.in_module(agents, list, module=chimera_main)
    result = runner.invoke(app, ['agent', 'ls'])
    assert result.exit_code == 0
    assert 'No agents running' in result.output
