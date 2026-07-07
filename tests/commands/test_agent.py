import os
import subprocess
from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace

from testfixtures import Replacer, ShouldRaise, TempDir, compare

from chimera import __main__ as chimera_main
from chimera.commands.agent import (
    Agent,
    agent,
    agents,
    all_sessions,
    in_goal,
    live_sessions,
    resume,
    scope_line,
    scoped,
    session_summary,
    under,
)
from chimera.config import ProjectConfig
from chimera.context import Project, Scope
from tests.cli import Command, action_logs


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
    expected = ['claude', '--name', 'proj@goal@agent']  # no bypass flag unless dangerous
    compare(calls, expected=[(expected, worktree, True)])


def test_agent_makes_bypass_reachable_when_dangerous(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    agent(worktree, 'proj@goal@agent', dangerous=True)
    expected = ['claude', '--name', 'proj@goal@agent', '--allow-dangerously-skip-permissions']
    compare(calls, expected=[(expected, worktree, True)])


def test_agent_runs_in_the_background_when_given_a_prompt(
    tmpdir: TempDir, replace: Replacer
) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    agent(worktree, 'proj@goal@agent', 'fix the bug')
    expected = ['claude', '--bg', '--name', 'proj@goal@agent', 'fix the bug']
    compare(calls, expected=[(expected, worktree, True)])


def test_agent_background_carries_bypass_when_dangerous(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    agent(worktree, 'proj@goal@agent', 'fix the bug', dangerous=True)
    expected = [
        'claude',
        '--bg',
        '--name',
        'proj@goal@agent',
        '--allow-dangerously-skip-permissions',
        'fix the bug',
    ]
    compare(calls, expected=[(expected, worktree, True)])


def test_agent_refuses_when_a_session_is_live(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace, sessions=[{'sessionId': 'abc123', 'status': 'idle'}])
    with ShouldRaise(
        RuntimeError(f'an agent is already live in {worktree}: abc123 (idle) — attach or stop it')
    ):
        agent(worktree, 'proj@goal@agent')
    compare(calls, expected=[])  # never launched


def test_agent_missing_worktree_raises(tmpdir: TempDir) -> None:
    with ShouldRaise(FileNotFoundError(tmpdir / 'nope')):
        agent(tmpdir / 'nope', 'x')


def test_agent_passes_extra_flags_through(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    agent(worktree, 'proj@goal@agent', extra=['--model', 'opus'])
    expected = ['claude', '--name', 'proj@goal@agent', '--model', 'opus']
    compare(calls, expected=[(expected, worktree, True)])


def test_agent_does_not_double_up_when_bypass_already_requested(
    tmpdir: TempDir, replace: Replacer
) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    agent(
        worktree,
        'proj@goal@agent',
        extra=['--allow-dangerously-skip-permissions'],
        dangerous=True,
    )
    expected = ['claude', '--name', 'proj@goal@agent', '--allow-dangerously-skip-permissions']
    compare(calls, expected=[(expected, worktree, True)])


def test_resume_runs_claude_resume_in_the_foreground_by_default(
    tmpdir: TempDir, replace: Replacer
) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    resume(worktree, 'proj@goal@agent')
    expected = ['claude', '--resume', 'proj@goal@agent']  # no bypass flag unless dangerous
    compare(calls, expected=[(expected, worktree, True)])


def test_resume_makes_bypass_reachable_when_dangerous(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    resume(worktree, 'proj@goal@agent', dangerous=True)
    expected = ['claude', '--resume', 'proj@goal@agent', '--allow-dangerously-skip-permissions']
    compare(calls, expected=[(expected, worktree, True)])


def test_resume_runs_in_the_background_when_given_a_prompt(
    tmpdir: TempDir, replace: Replacer
) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    resume(worktree, 'proj@goal@agent', 'carry on')
    expected = ['claude', '--bg', '--resume', 'proj@goal@agent', 'carry on']
    compare(calls, expected=[(expected, worktree, True)])


def test_resume_passes_extra_flags_through(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    resume(worktree, 'proj@goal@agent', extra=['--dangerously-skip-permissions'])
    expected = ['claude', '--resume', 'proj@goal@agent', '--dangerously-skip-permissions']
    compare(calls, expected=[(expected, worktree, True)])


def test_resume_refuses_when_a_session_is_live(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace, sessions=[{'sessionId': 'abc123', 'status': 'idle'}])
    with ShouldRaise(
        RuntimeError(f'an agent is already live in {worktree}: abc123 (idle) — attach or stop it')
    ):
        resume(worktree, 'proj@goal@agent')
    compare(calls, expected=[])  # never launched


def test_resume_missing_worktree_raises(tmpdir: TempDir) -> None:
    with ShouldRaise(FileNotFoundError(tmpdir / 'nope')):
        resume(tmpdir / 'nope', 'x')


def test_live_sessions_queries_claude_by_cwd(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    captured: dict[str, object] = {}
    pid = os.getpid()

    def fake_run(
        cmd: object, capture_output: bool = False, text: bool = False, check: bool = False
    ):
        captured['cmd'] = cmd
        return SimpleNamespace(stdout=f'[{{"sessionId": "x", "status": "idle", "pid": {pid}}}]')

    replace.in_module(subprocess.run, fake_run)
    compare(live_sessions(worktree), expected=[{'sessionId': 'x', 'status': 'idle', 'pid': pid}])
    compare(captured['cmd'], expected=['claude', 'agents', '--json', '--cwd', str(worktree)])


def test_all_sessions_queries_claude_unscoped(replace: Replacer) -> None:
    captured: dict[str, object] = {}
    pid = os.getpid()

    def fake_run(
        cmd: object, capture_output: bool = False, text: bool = False, check: bool = False
    ):
        captured['cmd'] = cmd
        return SimpleNamespace(stdout=f'[{{"sessionId": "x", "status": "idle", "pid": {pid}}}]')

    replace.in_module(subprocess.run, fake_run)
    compare(all_sessions(), expected=[{'sessionId': 'x', 'status': 'idle', 'pid': pid}])
    compare(captured['cmd'], expected=['claude', 'agents', '--json'])  # no --cwd → every project


def _dead(pid: int, sig: int) -> None:
    raise ProcessLookupError


def _foreign(pid: int, sig: int) -> None:
    raise PermissionError


def test_sessions_filters_out_an_entry_whose_pid_has_died(
    tmpdir: TempDir, replace: Replacer
) -> None:
    worktree = tmpdir.makedir('wt')
    replace.in_module(
        subprocess.run,
        lambda cmd, capture_output=False, text=False, check=False: SimpleNamespace(
            stdout='[{"sessionId": "x", "status": "idle", "pid": 999999}]'
        ),
    )
    replace.in_module(os.kill, _dead, module=os)
    compare(live_sessions(worktree), expected=[])


def test_sessions_filters_out_an_entry_with_no_pid_at_all(
    tmpdir: TempDir, replace: Replacer
) -> None:
    worktree = tmpdir.makedir('wt')
    replace.in_module(
        subprocess.run,
        lambda cmd, capture_output=False, text=False, check=False: SimpleNamespace(
            stdout='[{"kind": "background", "startedAt": 1781247747055, "name": "x"}]'
        ),
    )  # the degraded shape claude's registry reports briefly after a killed pid is pruned
    compare(live_sessions(worktree), expected=[])


def test_sessions_keeps_an_entry_whose_pid_belongs_to_another_user(
    tmpdir: TempDir, replace: Replacer
) -> None:
    worktree = tmpdir.makedir('wt')
    replace.in_module(
        subprocess.run,
        lambda cmd, capture_output=False, text=False, check=False: SimpleNamespace(
            stdout='[{"sessionId": "x", "status": "idle", "pid": 1}]'
        ),
    )
    replace.in_module(os.kill, _foreign, module=os)
    compare(live_sessions(worktree), expected=[{'sessionId': 'x', 'status': 'idle', 'pid': 1}])


def _project_with_worktree(tmpdir: TempDir) -> Path:
    project = tmpdir.makedir('myproject')
    tmpdir.dump('myproject/config.yaml', {'kind': 'project', 'repo': str(project)})
    (project / 'worktrees' / 'g@agent').mkdir(parents=True)
    os.chdir(project)  # the CLI infers the project (and its name) from cwd
    return project


def test_agent_start_cli(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    _project_with_worktree(tmpdir)
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@agent'  # cwd resolves symlinks like the wrapper
    command.run('agent', 'start', '-g', 'g').check(
        output=f'Launched agent in {expected}',
        logging=action_logs(
            'agent start',
            'chimera.commands.agent.agent',
            {'prompt': None, 'goal': 'g', 'actor': None, 'project': None, 'dangerous': False},
        ),
    )
    claude_cmd = ['claude', '--name', 'myproject@g@agent']  # no bypass flag by default
    compare(calls, expected=[(claude_cmd, expected, True)])


def test_agent_start_cli_dangerous_makes_bypass_reachable(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _project_with_worktree(tmpdir)
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    command.run('agent', 'start', '-g', 'g', '--dangerous').check(
        output=f'Launched agent in {expected}',
        logging=action_logs(
            'agent start',
            'chimera.commands.agent.agent',
            {'prompt': None, 'goal': 'g', 'actor': None, 'project': None, 'dangerous': True},
        ),
    )
    claude_cmd = ['claude', '--name', 'myproject@g@agent', '--allow-dangerously-skip-permissions']
    compare(calls, expected=[(claude_cmd, expected, True)])


def test_agent_start_cli_with_prompt(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    _project_with_worktree(tmpdir)
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    command.run('agent', 'start', 'do it', '-g', 'g').check(
        output=f'Launched agent in {expected}',
        logging=action_logs(
            'agent start',
            'chimera.commands.agent.agent',
            {'prompt': 'do it', 'goal': 'g', 'actor': None, 'project': None, 'dangerous': False},
        ),
    )
    claude_cmd = ['claude', '--bg', '--name', 'myproject@g@agent', 'do it']
    compare(calls, expected=[(claude_cmd, expected, True)])


def test_agent_start_cli_with_actor(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    project = _project_with_worktree(tmpdir)
    (project / 'worktrees' / 'g@reviewer').mkdir()
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@reviewer'
    command.run('agent', 'start', '-g', 'g', '-a', 'reviewer').check(
        output=f'Launched agent in {expected}',
        logging=action_logs(
            'agent start',
            'chimera.commands.agent.agent',
            {'prompt': None, 'goal': 'g', 'actor': 'reviewer', 'project': None, 'dangerous': False},
        ),
    )
    claude_cmd = ['claude', '--name', 'myproject@g@reviewer']
    compare(calls, expected=[(claude_cmd, expected, True)])


def test_agent_start_cli_forwards_flags_after_dashdash(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _project_with_worktree(tmpdir)
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    # no prompt, only passthrough: the flag must not be mistaken for the prompt
    command.run('agent', 'start', '-g', 'g', '--', '--dangerously-skip-permissions').check(
        output=f'Launched agent in {expected}',
        logging=action_logs(
            'agent start',
            'chimera.commands.agent.agent',
            {'prompt': None, 'goal': 'g', 'actor': None, 'project': None, 'dangerous': False},
        ),
    )
    claude_cmd = ['claude', '--name', 'myproject@g@agent', '--dangerously-skip-permissions']
    compare(calls, expected=[(claude_cmd, expected, True)])


def test_agent_start_cli_with_prompt_and_passthrough(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _project_with_worktree(tmpdir)
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    command.run('agent', 'start', 'do it', '-g', 'g', '--', '--model', 'opus').check(
        output=f'Launched agent in {expected}',
        logging=action_logs(
            'agent start',
            'chimera.commands.agent.agent',
            {'prompt': 'do it', 'goal': 'g', 'actor': None, 'project': None, 'dangerous': False},
        ),
    )
    claude_cmd = ['claude', '--bg', '--name', 'myproject@g@agent', '--model', 'opus', 'do it']
    compare(calls, expected=[(claude_cmd, expected, True)])


def test_agent_resume_cli(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    _project_with_worktree(tmpdir)
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    command.run('agent', 'resume', '-g', 'g').check(
        output=f'Resumed agent in {expected}',
        logging=action_logs(
            'agent resume',
            'chimera.commands.agent.resume',
            {'prompt': None, 'goal': 'g', 'actor': None, 'project': None, 'dangerous': False},
        ),
    )
    claude_cmd = ['claude', '--resume', 'myproject@g@agent']  # no bypass flag by default
    compare(calls, expected=[(claude_cmd, expected, True)])


def test_agent_resume_cli_with_passthrough(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _project_with_worktree(tmpdir)
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    command.run('agent', 'resume', '-g', 'g', '--', '--dangerously-skip-permissions').check(
        output=f'Resumed agent in {expected}',
        logging=action_logs(
            'agent resume',
            'chimera.commands.agent.resume',
            {'prompt': None, 'goal': 'g', 'actor': None, 'project': None, 'dangerous': False},
        ),
    )
    claude_cmd = ['claude', '--resume', 'myproject@g@agent', '--dangerously-skip-permissions']
    compare(calls, expected=[(claude_cmd, expected, True)])


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
    compare(session_summary('/work/proj', 'agent', projects), expected='fix the bug')


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
    compare(session_summary('/work/proj', 'agent', projects), expected='my title')


def test_session_summary_uses_ai_title_when_no_custom_title(tmpdir: TempDir) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(
        projects / '-work-proj',
        's.jsonl',
        '{"type": "last-prompt", "lastPrompt": "fix the bug"}\n'
        '{"type": "ai-title", "aiTitle": "ai topic"}\n',
        1000,
    )
    compare(session_summary('/work/proj', 'agent', projects), expected='ai topic')


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
    compare(session_summary('/work/proj', 'proj@goal@agent', projects), expected='fix the bug')


def test_session_summary_takes_latest_of_each_record(tmpdir: TempDir) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(
        projects / '-work-proj',
        's.jsonl',
        '{"type": "custom-title", "customTitle": "old name"}\n'
        '{"type": "custom-title", "customTitle": "new name"}\n',
        1000,
    )
    compare(session_summary('/work/proj', 'agent', projects), expected='new name')


def test_session_summary_skips_typed_record_missing_its_value(tmpdir: TempDir) -> None:
    projects = tmpdir.makedir('projects')
    _transcript(
        projects / '-work-proj',
        's.jsonl',
        # a last-prompt record may carry no lastPrompt field; fall through to what does
        '{"type": "last-prompt"}\n{"type": "ai-title", "aiTitle": "ai topic"}\n',
        1000,
    )
    compare(session_summary('/work/proj', 'agent', projects), expected='ai topic')


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
    compare(
        agents(projects),
        expected=[
            Agent(
                id='x',
                name='proj@goal@agent',
                status='busy',
                cwd=Path('/work/proj'),
                summary='do it',
            ),
            Agent(id='bare', name='bare', status='idle', cwd=Path('/elsewhere'), summary=None),
        ],
    )


def test_agents_tolerates_sessions_missing_fields(replace: Replacer) -> None:
    replace.in_module(
        all_sessions,
        # a session without status/cwd (e.g. a foreground session) must not crash the listing;
        # status falls back to state, then '?', and a missing cwd yields no summary
        lambda: [{'sessionId': 'lonely', 'state': 'working'}],
    )
    compare(
        agents(),
        expected=[Agent(id='lonely', name='lonely', status='working', cwd=Path('.'), summary=None)],
    )


def test_agent_detail_falls_back_to_tilde_cwd(replace: Replacer) -> None:
    replace.on_class(Path.home, lambda cls: Path('/home/me'))
    compare(Agent('i', 'n', 'idle', Path('/home/me/work'), 'a prompt').detail, expected='a prompt')
    compare(Agent('i', 'n', 'idle', Path('/home/me/work'), None).detail, expected='~/work')
    compare(Agent('i', 'n', 'idle', Path('/other'), None).detail, expected='/other')


def test_scoped_unpinned_keeps_every_agent_when_otherwise_is_none(tmpdir: TempDir) -> None:
    ws = tmpdir.makedir('lycia')
    inside = _agent_at(ws / 'proj' / 'worktrees' / 'g@agent', 'inside')
    outside = _agent_at(tmpdir / 'elsewhere', 'outside')
    compare(
        scoped([inside, outside], Scope(ws, None, None), otherwise=None), expected=[inside, outside]
    )


def test_scoped_unpinned_bounds_to_otherwise_when_given(tmpdir: TempDir) -> None:
    ws = tmpdir.makedir('lycia')
    inside = _agent_at(ws / 'proj' / 'worktrees' / 'g@agent', 'inside')
    outside = _agent_at(tmpdir / 'elsewhere', 'outside')
    compare(scoped([inside, outside], Scope(ws, None, None), otherwise=ws), expected=[inside])


def test_scoped_project_keeps_only_agents_under_the_project(tmpdir: TempDir) -> None:
    ws = tmpdir.makedir('lycia')
    project = _project_obj(ws / 'proj')
    inside = _agent_at(ws / 'proj' / 'worktrees' / 'g@agent', 'inside')
    other = _agent_at(ws / 'q' / 'worktrees' / 'g@agent', 'other')
    compare(scoped([inside, other], Scope(ws, project, None), otherwise=None), expected=[inside])


def test_scoped_goal_matches_every_actor_worktree_only(tmpdir: TempDir) -> None:
    ws = tmpdir.makedir('lycia')
    project = _project_obj(ws / 'proj')
    worktrees = ws / 'proj' / 'worktrees'
    agent_wt = _agent_at(worktrees / 'g@agent', 'agent')
    reviewer_sub = _agent_at(worktrees / 'g@reviewer' / 'src', 'reviewer')  # a subdir still counts
    other_goal = _agent_at(worktrees / 'gg@agent', 'other-goal')  # 'gg' must not match 'g'
    in_repo = _agent_at(ws / 'proj' / 'repo', 'repo')  # in the project, not a goal worktree
    listing = [agent_wt, reviewer_sub, other_goal, in_repo]
    compare(
        scoped(listing, Scope(ws, project, 'g'), otherwise=None), expected=[agent_wt, reviewer_sub]
    )


def test_scope_line_reports_the_pinned_target(tmpdir: TempDir) -> None:
    ws = tmpdir.makedir('lycia')
    project = _project_obj(ws / 'proj')
    compare(scope_line(Scope(ws, project, 'g')), expected='scope: proj@g')
    compare(scope_line(Scope(ws, project, None)), expected='scope: proj')
    compare(scope_line(Scope(ws, None, None)), expected='scope: all agents')


def test_under_and_in_goal(tmpdir: TempDir) -> None:
    root = tmpdir.makedir('r')
    compare(under(root, root), expected=True)
    compare(under(root / 'a' / 'b', root), expected=True)
    compare(under(tmpdir / 'other', root), expected=False)
    worktrees = tmpdir.makedir('wt')
    compare(in_goal(worktrees / 'g@agent', worktrees, 'g'), expected=True)
    compare(in_goal(worktrees / 'goal@agent', worktrees, 'g'), expected=False)  # 'g' ≠ 'goal'
    compare(in_goal(worktrees, worktrees, 'g'), expected=False)  # the dir itself is not in a goal


def _scoped_cli(tmpdir: TempDir, replace: Replacer) -> Path:
    ws = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    project = ws / 'proj'
    (project / 'worktrees' / 'g@agent').mkdir(parents=True)
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(project)})
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    return project


def test_agent_ls_cli_unpinned_lists_every_agent(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    project = _scoped_cli(tmpdir, replace)
    worktree = project / 'worktrees' / 'g@agent'
    replace.in_module(
        agents,
        lambda: [
            Agent(
                id='aaa11111', name='proj@g@agent', status='busy', cwd=worktree, summary='fix it'
            ),
            Agent(id='bbb22222', name='other', status='idle', cwd=worktree, summary='do a thing'),
            Agent(id='ccc', name='ccc', status='idle', cwd=worktree, summary='unnamed'),
            Agent(id='ddd', name='stray', status='idle', cwd=tmpdir / 'outside', summary='x'),
        ],
        module=chimera_main,
    )
    command.run('agent', 'ls').check(  # unpinned → every agent, even the outside stray
        output='\n'.join(
            [
                'scope: all agents',
                'aaa11111  proj@g@agent  busy  fix it',
                'bbb22222  other         idle  do a thing',
                'ccc                     idle  unnamed',  # name blanked: it merely echoes the id
                'ddd       stray         idle  x',
            ]
        ),
        logging=action_logs(
            'agent ls', 'chimera.commands.agent.scoped', {'project': None, 'goal': None}
        ),
    )


def test_agent_ls_cli_trims_long_detail(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    project = _scoped_cli(tmpdir, replace)
    worktree = project / 'worktrees' / 'g@agent'
    detail = 'x' * 200
    replace.in_module(
        agents,
        lambda: [Agent(id='aaa', name='named', status='busy', cwd=worktree, summary=detail)],
        module=chimera_main,
    )
    command.run('agent', 'ls').check(
        output='scope: all agents\naaa  named  busy  ' + 'x' * 79 + '…',
        logging=action_logs(
            'agent ls', 'chimera.commands.agent.scoped', {'project': None, 'goal': None}
        ),
    )


def test_agent_ls_cli_pinned_to_project_filters_strays(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    project = _scoped_cli(tmpdir, replace)
    worktree = project / 'worktrees' / 'g@agent'
    replace.in_module(
        agents,
        lambda: [
            Agent(id='aaa', name='proj@g@agent', status='busy', cwd=worktree, summary='fix it'),
            Agent(id='ddd', name='stray', status='idle', cwd=tmpdir / 'outside', summary='x'),
        ],
        module=chimera_main,
    )
    command.run('agent', 'ls', '-p', 'proj').check(
        output='scope: proj\naaa  proj@g@agent  busy  fix it',
        logging=action_logs(
            'agent ls', 'chimera.commands.agent.scoped', {'project': 'proj', 'goal': None}
        ),
    )


def test_agent_ls_cli_when_nothing_running(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _scoped_cli(tmpdir, replace)
    replace.in_module(agents, list, module=chimera_main)
    command.run('agent', 'ls').check(
        output='scope: all agents\nNo agents running',
        logging=action_logs(
            'agent ls', 'chimera.commands.agent.scoped', {'project': None, 'goal': None}
        ),
    )
