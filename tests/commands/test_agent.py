import os
import signal
import subprocess
import sys
from hashlib import sha256
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from testfixtures import Replacer, ShouldRaise, TempDir, compare

from chimera import __main__ as chimera_main
from chimera.agents import Session
from chimera.agents.claude import Claude
from chimera.agents.registry import AgentSpec
from chimera.archive import Archive
from chimera.archive import Session as ArchiveSession
from chimera.commands.agent import (
    agent,
    agents,
    in_goal,
    live,
    resume,
    resume_target,
    scope_line,
    scoped,
    shown,
    stop,
    under,
)
from chimera.agent_env import ROLE_AGENT
from chimera.config import ProjectConfig, UserError
from chimera.context import Project, Scope
from chimera.dry import Dry
from chimera.prime import prime
from tests.cli import (
    Command,
    action_logs,
    capture_env,
    context_sources,
    full_capture,
    sources_lines,
)


def _project_obj(directory: Path) -> Project:
    return Project(directory, ProjectConfig(kind='project', repo=Path('/r')))


def _agent_at(cwd: Path, name: str = 'a') -> Session:
    return Session(name, name, 'idle', cwd, None)


def _stub(replace: Replacer, sessions: Iterable[Session] = ()) -> list[object]:
    calls: list[object] = []
    replace.on_class(Claude.live, lambda self, cwd=None: list(sessions))
    replace.in_module(
        subprocess.run,
        lambda cmd, cwd=None, check=False, env=None: calls.append((cmd, cwd, check)),
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
    calls = _stub(replace, sessions=[_agent_at(worktree, 'abc123')])
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
    calls = _stub(replace, sessions=[_agent_at(worktree, 'abc123')])
    with ShouldRaise(
        RuntimeError(f'an agent is already live in {worktree}: abc123 (idle) — attach or stop it')
    ):
        resume(worktree, 'proj@goal@agent')
    compare(calls, expected=[])  # never launched


def test_resume_missing_worktree_raises(tmpdir: TempDir) -> None:
    with ShouldRaise(FileNotFoundError(tmpdir / 'nope')):
        resume(tmpdir / 'nope', 'x')


def test_resume_by_archived_id_reasserts_the_canonical_name(
    tmpdir: TempDir, replace: Replacer
) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    resume(worktree, 'proj@goal@agent', id='11111111-2222-3333-4444-555555555555')
    expected = [
        'claude',
        '--resume',
        '11111111-2222-3333-4444-555555555555',
        '--name',
        'proj@goal@agent',
    ]
    compare(calls, expected=[(expected, worktree, True)])


def _address_archived(
    workspace: Path, native_id: str, name: str, project: str = 'myproject'
) -> None:
    """A recorded session for ``<project>@g@agent`` — under whatever display name."""
    with Archive.open(workspace / 'state' / 'archive.db') as store:
        store.record_session(
            ArchiveSession(
                platform='claude',
                native_id=native_id,
                status='other',
                started_at=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
                name=name,
                project=project,
                goal='g',
                actor='agent',
            )
        )


def test_resume_target_answers_from_the_archive_despite_a_rename(
    tmpdir: TempDir, replace: Replacer
) -> None:
    ws = tmpdir.makedir('ws')
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    _address_archived(ws, 'uuid-renamed', name='fun UI rename')  # canonical name long gone
    assert resume_target(ws, 'claude', 'myproject', 'g', 'agent') == 'uuid-renamed'


def test_resume_target_is_none_for_an_unseen_address(tmpdir: TempDir, replace: Replacer) -> None:
    ws = tmpdir.makedir('ws')
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    assert resume_target(ws, 'claude', 'myproject', 'g', 'agent') is None


def test_resume_target_is_none_outside_any_workspace(tmpdir: TempDir) -> None:
    assert resume_target(tmpdir.path, 'claude', 'myproject', 'g', 'agent') is None


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


def test_agent_resume_cli_resolves_the_session_through_the_archive(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    # the field failure this guards: a UI rename left the canonical name unfindable in
    # the registry — the archive answers the address by immutable id instead
    ws = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    project = ws / 'proj'
    (project / 'worktrees' / 'g@agent').mkdir(parents=True)
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(project)})
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    os.chdir(project)
    _address_archived(ws, 'uuid-1234', name='renamed in the UI', project='proj')
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    digest = sha256(AGENT_ROLE_TEXT.encode()).hexdigest()
    context = ws / 'state' / 'context' / f'proj@g@agent-{digest[:8]}.md'
    command.run('agent', 'resume', '-g', 'g').check(
        output=f'Resumed agent in {expected}',
        logging=[
            {
                'level': 'INFO',
                'command': 'agent resume',
                'phase': 'start',
                'function': 'chimera.commands.agent.resume',
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
                'sources': context_sources(ws, 'agent', pinned=project.resolve()),
                'message': 'context: rendered',
            },
            {
                'level': 'INFO',
                'platform': 'claude',
                'native_id': 'uuid-1234',
                'project': 'proj',
                'goal': 'g',
                'actor': 'agent',
                'message': 'agent resume: archived session',
            },
            {'level': 'INFO', 'command': 'agent resume', 'phase': 'end'},
        ],
    )
    claude_cmd = [
        'claude',
        '--resume',
        'uuid-1234',
        '--name',
        'proj@g@agent',
        '--append-system-prompt-file',
        str(context),
    ]
    compare(calls, expected=[(claude_cmd, expected, True)])


def test_stop_is_keyed_by_worktree_so_a_rename_cannot_hide_a_session(
    tmpdir: TempDir, replace: Replacer
) -> None:
    # stop never selects by name: liveness and pids come from the registry by cwd,
    # so a session renamed in the UI is still found and stopped
    worktree = tmpdir.makedir('wt')
    pid = subprocess.run(
        ['bash', '-c', 'sleep 60 & echo $!'], capture_output=True, text=True, check=True
    )
    renamed = Session('uuid-1', 'renamed in the UI', 'idle', worktree, None, pid=int(pid.stdout))
    replace.on_class(Claude.live, lambda self, cwd=None: [renamed] if cwd == worktree else [])
    try:
        # stop() itself proves the kill: it waits for the pid to die and raises otherwise
        compare(stop(worktree), expected=[renamed])
    finally:
        try:
            os.kill(int(pid.stdout), signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_agents_aggregates_registered_harnesses(replace: Replacer) -> None:
    # the sole registered harness today is claude
    lonely = Session(id='lonely', name='lonely', status='working', cwd=Path('.'), summary=None)
    replace.on_class(Claude.sessions, lambda self: [lonely])
    compare(agents(), expected=[lonely])


def _ghost_at(cwd: Path, name: str = 'ghost') -> Session:
    return Session(name, name, 'idle', cwd, None, stale='claimed pid 999 is not running')


def test_shown_default_withholds_stale_sessions(tmpdir: TempDir) -> None:
    running, ghost = _agent_at(tmpdir / 'wt'), _ghost_at(tmpdir / 'wt')
    compare(shown([running, ghost], verbose=False), expected=([running], 1))


def test_shown_verbose_withholds_nothing(tmpdir: TempDir) -> None:
    running, ghost = _agent_at(tmpdir / 'wt'), _ghost_at(tmpdir / 'wt')
    compare(shown([running, ghost], verbose=True), expected=([running, ghost], 0))


def test_shown_default_with_nothing_stale_withholds_nothing(tmpdir: TempDir) -> None:
    running = _agent_at(tmpdir / 'wt')
    compare(shown([running], verbose=False), expected=([running], 0))


def test_live_aggregates_registered_harnesses(tmpdir: TempDir, replace: Replacer) -> None:
    # cleanup's question is "is *any* harness's agent live here" — claude's answer flows through
    worktree = tmpdir.makedir('wt')
    session = _agent_at(worktree, 'busy-one')
    replace.on_class(Claude.live, lambda self, cwd=None: [session] if cwd == worktree else [])
    compare(live(worktree), expected=[session])


def test_extra_bypass_flags_refused_under_an_ai_agent(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    replace.in_environ('CLAUDECODE', '1')
    calls = _stub(replace)
    refused = UserError(
        '--dangerously-skip-permissions: not available when chimera is driven by an AI agent'
    )
    with ShouldRaise(refused):
        agent(worktree, 'n', extra=['--dangerously-skip-permissions'])
    with ShouldRaise(refused):
        resume(worktree, 'n', extra=['--dangerously-skip-permissions'])
    compare(calls, expected=[])  # never launched


def test_extra_bypass_flags_refused_under_a_role_stamp_alone(
    tmpdir: TempDir, replace: Replacer
) -> None:
    # no CLAUDECODE (conftest clears it): the role stamp alone marks the AI session
    worktree = tmpdir.makedir('wt')
    replace.in_environ('CHIMERA_ROLE', 'manager')
    calls = _stub(replace)
    with ShouldRaise(
        UserError(
            '--dangerously-skip-permissions: not available when chimera is driven by an AI agent'
        )
    ):
        agent(worktree, 'n', extra=['--dangerously-skip-permissions'])
    compare(calls, expected=[])  # never launched


def test_extra_bypass_flags_pass_for_a_human(tmpdir: TempDir, replace: Replacer) -> None:
    # conftest clears CLAUDECODE: the same passthrough launches untouched for a human
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    agent(worktree, 'n', extra=['--allow-dangerously-skip-permissions'])
    expected = ['claude', '--name', 'n', '--allow-dangerously-skip-permissions']
    compare(calls, expected=[(expected, worktree, True)])


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
            'agent ls',
            'chimera.commands.agent.scoped',
            {'verbose': False, 'project': None, 'goal': None},
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
            'agent ls',
            'chimera.commands.agent.scoped',
            {'verbose': False, 'project': None, 'goal': None},
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
            'agent ls',
            'chimera.commands.agent.scoped',
            {'verbose': False, 'project': 'proj', 'goal': None},
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
            'agent ls',
            'chimera.commands.agent.scoped',
            {'verbose': False, 'project': None, 'goal': None},
        ),
    )


def test_agent_ls_cli_default_withholds_stale_and_hints(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    project = _scoped_cli(tmpdir, replace)
    worktree = project / 'worktrees' / 'g@agent'
    replace.in_module(
        agents,
        lambda: [
            Session(id='aaa', name='proj@g@agent', status='busy', cwd=worktree, summary='fix it'),
            _ghost_at(worktree, 'bbb'),
        ],
        module=chimera_main,
    )
    command.run('agent', 'ls').check(  # the stale row never shows; only the hint betrays it
        output='\n'.join(
            [
                'scope: all agents',
                'aaa  proj@g@agent  busy  fix it',
                '(+1 stale session — ch agent ls -v to show)',
            ]
        ),
        logging=action_logs(
            'agent ls',
            'chimera.commands.agent.scoped',
            {'verbose': False, 'project': None, 'goal': None},
        ),
    )


def test_agent_ls_cli_verbose_shows_stale_with_reason(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    project = _scoped_cli(tmpdir, replace)
    worktree = project / 'worktrees' / 'g@agent'
    replace.in_module(
        agents,
        lambda: [
            Session(id='aaa', name='proj@g@agent', status='busy', cwd=worktree, summary='fix it'),
            _ghost_at(worktree, 'bbb'),
        ],
        module=chimera_main,
    )
    command.run('agent', 'ls', '-v').check(  # live rows unchanged; no hint — nothing is hidden
        output='\n'.join(
            [
                'scope: all agents',
                'aaa  proj@g@agent  busy   fix it',
                'bbb                stale  claimed pid 999 is not running',  # name blanked: echoes id
            ]
        ),
        logging=action_logs(
            'agent ls',
            'chimera.commands.agent.scoped',
            {'verbose': True, 'project': None, 'goal': None},
        ),
    )


def test_agent_ls_cli_only_stale_reports_nothing_running_plus_hint(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    project = _scoped_cli(tmpdir, replace)
    worktree = project / 'worktrees' / 'g@agent'
    replace.in_module(
        agents,
        lambda: [_ghost_at(worktree, 'bbb'), _ghost_at(worktree, 'ccc')],
        module=chimera_main,
    )
    command.run('agent', 'ls').check(
        output='\n'.join(
            [
                'scope: all agents',
                'No agents running',
                '(+2 stale sessions — ch agent ls -v to show)',
            ]
        ),
        logging=action_logs(
            'agent ls',
            'chimera.commands.agent.scoped',
            {'verbose': False, 'project': None, 'goal': None},
        ),
    )


def test_agent_ls_cli_out_of_scope_stale_earns_no_hint(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    project = _scoped_cli(tmpdir, replace)
    worktree = project / 'worktrees' / 'g@agent'
    replace.in_module(
        agents,
        lambda: [
            Session(id='aaa', name='proj@g@agent', status='busy', cwd=worktree, summary='fix it'),
            _ghost_at(tmpdir / 'outside', 'bbb'),  # scoped out before the withhold count
        ],
        module=chimera_main,
    )
    command.run('agent', 'ls', '-p', 'proj').check(
        output='scope: proj\naaa  proj@g@agent  busy  fix it',
        logging=action_logs(
            'agent ls',
            'chimera.commands.agent.scoped',
            {'verbose': False, 'project': 'proj', 'goal': None},
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


# The role section leading every launched agent's context: the whole agent prime, pushed
# so the session never has to guess to pull it (see agent-docs/workspace-layout.md).
AGENT_ROLE_TEXT = f'# Role: agent\n\n{prime(ROLE_AGENT, project="proj", goal="g")}'


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
    digest = sha256(AGENT_ROLE_TEXT.encode()).hexdigest()
    context = ws / 'state' / 'context' / f'proj@g@agent-{digest[:8]}.md'
    command.run('agent', 'start', '-g', 'g').check(
        output=f'Launched agent in {expected}',
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
                'sources': context_sources(ws, 'agent', pinned=project.resolve()),
                'message': 'context: rendered',
            },
            {'level': 'INFO', 'command': 'agent start', 'phase': 'end'},
        ],
    )
    claude_cmd = [
        'claude',
        '--name',
        'proj@g@agent',
        '--model',
        'ws-model',
        '--append-system-prompt-file',
        str(context),
    ]
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
    principle = ws / 'principles' / 'verify.md'
    text = (
        f'{AGENT_ROLE_TEXT}\n\n# Principles\n\n'
        f'<!-- {principle.resolve()} (workspace) -->\nVerify before done.'
    )
    digest = sha256(text.encode()).hexdigest()
    context = ws / 'state' / 'context' / f'proj@g@agent-{digest[:8]}.md'
    sources = context_sources(ws, 'agent', pinned=project.resolve())
    sources[str(ws / 'principles' / '*.md')] = [str(principle)]
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
                'sources': sources,
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
    principle = ws / 'principles' / 'verify.md'
    text_ = (
        f'{AGENT_ROLE_TEXT}\n\n# Principles\n\n'
        f'<!-- {principle.resolve()} (workspace) -->\nVerify before done.'
    )
    digest = sha256(text_.encode()).hexdigest()
    context = ws / 'state' / 'context' / f'proj@g@agent-{digest[:8]}.md'
    sources = context_sources(ws, 'agent', pinned=project.resolve())
    sources[str(ws / 'principles' / '*.md')] = [str(principle)]
    command.run('agent', 'start', 'do it', '-g', 'g', '-m', 'opus', '--dry').check(
        output='\n'.join(
            [
                f'Would launch agent in {expected_wt}',
                'harness: claude  model: opus',
                'role: agent (scope: proj@g)',
                'prompt: do it',
                *sources_lines(sources),
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
                'sources': sources,
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
                'session: (no archived id — by name)',
                'harness: claude',
                'role: agent (scope: myproject@g)',
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


def test_agent_env_overlay_reaches_the_adapter(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    envs = capture_env(replace)
    agent(worktree, 'n', env={'CHIMERA_ROLE': 'agent'})
    resume(worktree, 'n', env={'CHIMERA_ROLE': 'agent'})
    compare(envs, expected=[{'CHIMERA_ROLE': 'agent'}, {'CHIMERA_ROLE': 'agent'}])


def test_agent_env_defaults_to_empty(tmpdir: TempDir, replace: Replacer) -> None:
    envs = capture_env(replace)
    agent(tmpdir.makedir('wt'), 'n')
    compare(envs, expected=[{}])


def test_agent_start_cli_stamps_the_agent_role(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _project_with_worktree(tmpdir)
    envs = capture_env(replace)
    command.run('agent', 'start', '-g', 'g')
    compare(envs, expected=[{'CHIMERA_ROLE': 'agent', 'CHIMERA_ROLE_SCOPE': 'myproject@g'}])


def test_agent_resume_cli_stamps_the_agent_role(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _project_with_worktree(tmpdir)
    envs = capture_env(replace)
    command.run('agent', 'resume', '-g', 'g')
    compare(envs, expected=[{'CHIMERA_ROLE': 'agent', 'CHIMERA_ROLE_SCOPE': 'myproject@g'}])


def _orphan_sleeper() -> int:
    """A sleeping process that is not our child, so SIGTERM leaves no zombie to confuse
    the exit polling in ``_terminate``."""
    out = subprocess.run(
        ['bash', '-c', 'sleep 60 & echo $!'], capture_output=True, text=True, check=True
    )
    return int(out.stdout)


def _session_with(pid: int | None, cwd: Path, name: str = 'p@g@agent') -> Session:
    return Session('x', name, 'idle', cwd, None, pid=pid)


def test_stop_terminates_the_live_session(tmpdir: TempDir, replace: Replacer) -> None:
    pid = _orphan_sleeper()
    session = _session_with(pid, tmpdir.path)
    replace.in_module(live, lambda worktree: [session])
    try:
        with full_capture() as log:
            compare(stop(tmpdir.path), expected=[session])
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    with ShouldRaise(ProcessLookupError):
        os.kill(pid, 0)
    log.check({'level': 'INFO', 'session': 'p@g@agent', 'pid': pid, 'message': 'agent stop'})


def test_stop_handles_a_session_that_already_died(tmpdir: TempDir, replace: Replacer) -> None:
    proc = subprocess.Popen(['true'])
    proc.wait()  # reaped: the pid no longer names a process
    session = _session_with(proc.pid, tmpdir.path)
    replace.in_module(live, lambda worktree: [session])
    compare(stop(tmpdir.path), expected=[session])


def test_stop_refuses_a_pidless_session(tmpdir: TempDir, replace: Replacer) -> None:
    replace.in_module(live, lambda worktree: [_session_with(None, tmpdir.path)])
    with ShouldRaise(
        UserError('p@g@agent reports no pid — stop it from its own harness, then re-run')
    ):
        stop(tmpdir.path)


def test_stop_refuses_a_session_that_will_not_die(tmpdir: TempDir, replace: Replacer) -> None:
    proc = subprocess.Popen(
        [
            sys.executable,
            '-c',
            'import signal, sys, time\n'
            'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
            'print("ready", flush=True)\n'
            'time.sleep(60)',
        ],
        stdout=subprocess.PIPE,
    )
    assert proc.stdout is not None
    proc.stdout.readline()  # the handler is installed
    replace.in_module(live, lambda worktree: [_session_with(proc.pid, tmpdir.path)])
    try:
        with ShouldRaise(
            UserError(
                f'p@g@agent (pid {proc.pid}) is still running 0.2s after SIGTERM — '
                f'kill it by hand, then re-run'
            )
        ):
            stop(tmpdir.path, timeout=0.2)
    finally:
        proc.kill()
        proc.wait()


def test_stop_dry_signals_nothing(tmpdir: TempDir, replace: Replacer) -> None:
    session = _session_with(os.getpid(), tmpdir.path)  # us: a signal would be very visible
    replace.in_module(live, lambda worktree: [session])
    compare(stop(tmpdir.path, Dry(True)), expected=[session])


def test_agent_stop_cli_dry(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    _project_with_worktree(tmpdir)
    worktree = Path.cwd() / 'worktrees' / 'g@agent'
    replace.in_module(live, lambda w: [_session_with(4242, worktree, 'myproject@g@agent')])
    command.run('agent', 'stop', '-g', 'g', '--dry').check(
        output='Would stop myproject@g@agent (pid 4242)',
        logging=action_logs(
            'agent stop',
            'chimera.commands.agent.stop',
            {'goal': 'g', 'actor': None, 'dry': True, 'project': None},
        ),
    )


def test_agent_stop_cli_with_nothing_live(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _project_with_worktree(tmpdir)
    replace.in_module(live, lambda w: [])
    worktree = Path.cwd() / 'worktrees' / 'g@agent'
    command.run('agent', 'stop', '-g', 'g').check(
        output=f'No live agent in {worktree}',
        logging=action_logs(
            'agent stop',
            'chimera.commands.agent.stop',
            {'goal': 'g', 'actor': None, 'dry': False, 'project': None},
        ),
    )


def test_stop_refuses_a_session_that_is_not_ours_to_signal(
    tmpdir: TempDir, replace: Replacer
) -> None:
    replace.in_module(live, lambda worktree: [_session_with(4242, tmpdir.path)])

    def deny(pid: int, sig: int) -> None:
        raise PermissionError(1, 'Operation not permitted')

    replace(target=os.kill, container=os, name='kill', replacement=deny)
    with ShouldRaise(
        UserError('p@g@agent (pid 4242) is not ours to signal — stop it by hand, then re-run')
    ):
        stop(tmpdir.path)


def test_stop_handles_the_pid_reused_by_another_user(tmpdir: TempDir, replace: Replacer) -> None:
    session = _session_with(4242, tmpdir.path)
    replace.in_module(live, lambda worktree: [session])

    def kill(pid: int, sig: int) -> None:
        if sig == 0:  # the SIGTERM freed the pid; another user's process now wears it
            raise PermissionError(1, 'Operation not permitted')

    replace(target=os.kill, container=os, name='kill', replacement=kill)
    compare(stop(tmpdir.path), expected=[session])


def test_stop_refuses_a_missing_worktree(tmpdir: TempDir) -> None:
    with ShouldRaise(
        UserError(f'no worktree at {tmpdir / "ghost@agent"} — check the goal (-g) and actor (-a)')
    ):
        stop(tmpdir / 'ghost@agent')


def test_agent_stop_cli_refuses_a_missing_worktree(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _project_with_worktree(tmpdir)
    worktree = Path.cwd() / 'worktrees' / 'ghost@agent'
    error = f'no worktree at {worktree} — check the goal (-g) and actor (-a)'
    command.run('agent', 'stop', '-g', 'ghost').check(
        output=f'Error: {error}',
        logging=action_logs(
            'agent stop',
            'chimera.commands.agent.stop',
            {'goal': 'ghost', 'actor': None, 'dry': False, 'project': None},
            error=f'UserError: {error}',
        ),
        return_code=1,
    )
