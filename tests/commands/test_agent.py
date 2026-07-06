import os
import subprocess
from hashlib import sha256
from collections.abc import Iterable
from pathlib import Path

from testfixtures import Replacer, ShouldRaise, TempDir, compare

from chimera import __main__ as chimera_main
from chimera.agents import Session
from chimera.agents.claude import all_sessions, live_sessions
from chimera.agents.registry import AgentSpec
from chimera.commands.agent import (
    agent,
    agents,
    in_goal,
    resume,
    scope_line,
    scoped,
    under,
)
from chimera.config import ProjectConfig
from chimera.context import Project, Scope
from tests.cli import Command, action_logs


def _project_obj(directory: Path) -> Project:
    return Project(directory, ProjectConfig(kind='project', repo=Path('/r')))


def _agent_at(cwd: Path, name: str = 'a') -> Session:
    return Session(name, name, 'idle', cwd, None)


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
            {
                'prompt': None,
                'goal': 'g',
                'actor': None,
                'project': None,
                'dangerous': False,
                'harness': None,
                'model': None,
                'dry': False,
            },
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
            {
                'prompt': None,
                'goal': 'g',
                'actor': None,
                'project': None,
                'dangerous': True,
                'harness': None,
                'model': None,
                'dry': False,
            },
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
            {
                'prompt': 'do it',
                'goal': 'g',
                'actor': None,
                'project': None,
                'dangerous': False,
                'harness': None,
                'model': None,
                'dry': False,
            },
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
            {
                'prompt': None,
                'goal': 'g',
                'actor': 'reviewer',
                'project': None,
                'dangerous': False,
                'harness': None,
                'model': None,
                'dry': False,
            },
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
            {
                'prompt': None,
                'goal': 'g',
                'actor': None,
                'project': None,
                'dangerous': False,
                'harness': None,
                'model': None,
                'dry': False,
            },
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
            {
                'prompt': 'do it',
                'goal': 'g',
                'actor': None,
                'project': None,
                'dangerous': False,
                'harness': None,
                'model': None,
                'dry': False,
            },
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
            {
                'prompt': None,
                'goal': 'g',
                'actor': None,
                'project': None,
                'dangerous': False,
                'harness': None,
                'model': None,
                'dry': False,
            },
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
            {
                'prompt': None,
                'goal': 'g',
                'actor': None,
                'project': None,
                'dangerous': False,
                'harness': None,
                'model': None,
                'dry': False,
            },
        ),
    )
    claude_cmd = ['claude', '--resume', 'myproject@g@agent', '--dangerously-skip-permissions']
    compare(calls, expected=[(claude_cmd, expected, True)])


def test_agents_aggregates_registered_harnesses(replace: Replacer) -> None:
    # the sole registered harness today is claude; its sessions come back enriched
    replace.in_module(all_sessions, lambda: [{'sessionId': 'lonely', 'state': 'working'}])
    compare(
        agents(),
        expected=[
            Session(id='lonely', name='lonely', status='working', cwd=Path('.'), summary=None)
        ],
    )


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
    assert under(root, root)
    assert under(root / 'a' / 'b', root)
    assert not under(tmpdir / 'other', root)
    worktrees = tmpdir.makedir('wt')
    assert in_goal(worktrees / 'g@agent', worktrees, 'g')
    assert not in_goal(worktrees / 'goal@agent', worktrees, 'g')  # 'g' ≠ 'goal'
    assert not in_goal(worktrees, worktrees, 'g')  # the dir itself is not in a goal


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
            Session(  # a full-UUID id renders as its 8-char short form
                id='aaa11111-9f80-4c8e-b3d7-1234567890ab',
                name='proj@g@agent',
                status='busy',
                cwd=worktree,
                summary='fix it',
            ),
            Session(id='bbb22222', name='other', status='idle', cwd=worktree, summary='do a thing'),
            Session(id='ccc', name='ccc', status='idle', cwd=worktree, summary='unnamed'),
            Session(id='ddd', name='stray', status='idle', cwd=tmpdir / 'outside', summary='x'),
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
        lambda: [Session(id='aaa', name='named', status='busy', cwd=worktree, summary=detail)],
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
            Session(id='aaa', name='proj@g@agent', status='busy', cwd=worktree, summary='fix it'),
            Session(id='ddd', name='stray', status='idle', cwd=tmpdir / 'outside', summary='x'),
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


def test_agent_spec_model_rides_as_model_flag(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    agent(worktree, 'proj@goal@agent', spec=AgentSpec('claude', 'opus'))
    expected = ['claude', '--name', 'proj@goal@agent', '--model', 'opus']
    compare(calls, expected=[(expected, worktree, True)])


def test_agent_passthrough_model_beats_spec_model(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    agent(
        worktree, 'proj@goal@agent', extra=['--model', 'sonnet'], spec=AgentSpec('claude', 'opus')
    )
    expected = ['claude', '--name', 'proj@goal@agent', '--model', 'sonnet']
    compare(calls, expected=[(expected, worktree, True)])


def test_resume_spec_model_rides_as_model_flag(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    resume(worktree, 'proj@goal@agent', spec=AgentSpec('claude', 'opus'))
    expected = ['claude', '--resume', 'proj@goal@agent', '--model', 'opus']
    compare(calls, expected=[(expected, worktree, True)])


def test_agent_start_cli_with_model_flag(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _project_with_worktree(tmpdir)
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    command.run('agent', 'start', '-g', 'g', '-m', 'opus').check(
        output=f'Launched agent in {expected}',
        logging=action_logs(
            'agent start',
            'chimera.commands.agent.agent',
            {
                'prompt': None,
                'goal': 'g',
                'actor': None,
                'project': None,
                'dangerous': False,
                'harness': None,
                'model': 'opus',
                'dry': False,
            },
        ),
    )
    claude_cmd = ['claude', '--name', 'myproject@g@agent', '--model', 'opus']
    compare(calls, expected=[(claude_cmd, expected, True)])


def test_agent_start_cli_model_from_project_config(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    project = _project_with_worktree(tmpdir)
    tmpdir.dump(
        project / 'config.yaml',
        {'kind': 'project', 'repo': str(project), 'agent': {'model': 'sonnet'}},
    )
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    command.run('agent', 'start', '-g', 'g').check(
        output=f'Launched agent in {expected}',
        logging=action_logs(
            'agent start',
            'chimera.commands.agent.agent',
            {
                'prompt': None,
                'goal': 'g',
                'actor': None,
                'project': None,
                'dangerous': False,
                'harness': None,
                'model': None,
                'dry': False,
            },
        ),
    )
    claude_cmd = ['claude', '--name', 'myproject@g@agent', '--model', 'sonnet']
    compare(calls, expected=[(claude_cmd, expected, True)])


def test_agent_start_cli_model_from_workspace_config(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace', 'agent': {'model': 'ws-model'}})
    project = ws / 'proj'
    (project / 'worktrees' / 'g@agent').mkdir(parents=True)
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(project)})
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    os.chdir(project)
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    command.run('agent', 'start', '-g', 'g').check(
        output=f'Launched agent in {expected}',
        logging=action_logs(
            'agent start',
            'chimera.commands.agent.agent',
            {
                'prompt': None,
                'goal': 'g',
                'actor': None,
                'project': None,
                'dangerous': False,
                'harness': None,
                'model': None,
                'dry': False,
            },
        ),
    )
    claude_cmd = ['claude', '--name', 'proj@g@agent', '--model', 'ws-model']
    compare(calls, expected=[(claude_cmd, expected, True)])


def test_agent_start_cli_unknown_harness_errors(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _project_with_worktree(tmpdir)
    calls = _stub(replace)
    command.run('agent', 'start', '-g', 'g', '--harness', 'codex').check(
        output="Error: no harness 'codex' (available: claude)",
        return_code=1,
        logging=action_logs(
            'agent start',
            'chimera.commands.agent.agent',
            {
                'prompt': None,
                'goal': 'g',
                'actor': None,
                'project': None,
                'dangerous': False,
                'harness': 'codex',
                'model': None,
                'dry': False,
            },
            error="UnknownHarnessError: no harness 'codex' (available: claude)",
        ),
    )
    compare(calls, expected=[])  # never launched


def test_agent_context_rides_as_system_prompt_file(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    agent(worktree, 'proj@goal@agent', context=tmpdir / 'ctx.md')
    expected = [
        'claude',
        '--name',
        'proj@goal@agent',
        '--append-system-prompt-file',
        str(tmpdir / 'ctx.md'),
    ]
    compare(calls, expected=[(expected, worktree, True)])


def test_agent_start_cli_injects_rendered_context(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    tmpdir.write(ws / 'principles' / 'verify.md', 'Verify before done.\n')
    project = ws / 'proj'
    (project / 'worktrees' / 'g@agent').mkdir(parents=True)
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(project)})
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    os.chdir(project)
    calls = _stub(replace)
    expected_wt = Path.cwd() / 'worktrees' / 'g@agent'
    text = '# Principles\n\nVerify before done.'
    digest = sha256(text.encode()).hexdigest()
    context = ws / 'logs' / 'context' / f'proj@g@agent-{digest[:8]}.md'
    command.run('agent', 'start', '-g', 'g').check(
        output=f'Launched agent in {expected_wt}',
        logging=[
            {
                'level': 'INFO',
                'command': 'agent start',
                'phase': 'start',
                'function': 'chimera.commands.agent.agent',
                'params': {
                    'prompt': None,
                    'goal': 'g',
                    'actor': None,
                    'project': None,
                    'dangerous': False,
                    'harness': None,
                    'model': None,
                    'dry': False,
                },
            },
            {
                'level': 'INFO',
                'session': 'proj@g@agent',
                'path': str(context),
                'sha256': digest,
                'message': 'context: rendered',
            },
            {'level': 'INFO', 'command': 'agent start', 'phase': 'end'},
        ],
    )
    compare(context.read_text(), expected=text)
    claude_cmd = ['claude', '--name', 'proj@g@agent', '--append-system-prompt-file', str(context)]
    compare(calls, expected=[(claude_cmd, expected_wt, True)])


def test_agent_start_cli_dry_previews_without_launching(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    tmpdir.write(ws / 'principles' / 'verify.md', 'Verify before done.\n')
    project = ws / 'proj'
    (project / 'worktrees' / 'g@agent').mkdir(parents=True)
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(project)})
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    os.chdir(project)
    calls = _stub(replace)
    expected_wt = Path.cwd() / 'worktrees' / 'g@agent'
    text_ = '# Principles\n\nVerify before done.'
    digest = sha256(text_.encode()).hexdigest()
    context = ws / 'logs' / 'context' / f'proj@g@agent-{digest[:8]}.md'
    command.run('agent', 'start', 'do it', '-g', 'g', '-m', 'opus', '--dry').check(
        output='\n'.join(
            [
                f'Would launch agent in {expected_wt}',
                'harness: claude  model: opus',
                'prompt: do it',
                f'context: {context}',
                '---',
                text_,
            ]
        ),
        logging=[
            {
                'level': 'INFO',
                'command': 'agent start',
                'phase': 'start',
                'function': 'chimera.commands.agent.agent',
                'params': {
                    'prompt': 'do it',
                    'goal': 'g',
                    'actor': None,
                    'project': None,
                    'dangerous': False,
                    'harness': None,
                    'model': 'opus',
                    'dry': True,
                },
            },
            {
                'level': 'INFO',
                'session': 'proj@g@agent',
                'path': str(context),
                'sha256': digest,
                'message': 'context: rendered',
            },
            {'level': 'INFO', 'command': 'agent start', 'phase': 'end'},
        ],
    )
    compare(calls, expected=[])  # nothing launched


def test_agent_resume_cli_dry_without_context(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _project_with_worktree(tmpdir)
    calls = _stub(replace)
    expected_wt = Path.cwd() / 'worktrees' / 'g@agent'
    # no workspace, no sources: the preview shows an interactive launch with no context
    command.run('agent', 'resume', '-g', 'g', '--dry', '--', '--verbose').check(
        output='\n'.join(
            [
                f'Would resume agent in {expected_wt}',
                'harness: claude',
                'prompt: (interactive)',
                'passthrough: --verbose',
                'context: (none)',
            ]
        ),
        logging=action_logs(
            'agent resume',
            'chimera.commands.agent.resume',
            {
                'prompt': None,
                'goal': 'g',
                'actor': None,
                'project': None,
                'dangerous': False,
                'harness': None,
                'model': None,
                'dry': True,
            },
        ),
    )
    compare(calls, expected=[])  # nothing launched
