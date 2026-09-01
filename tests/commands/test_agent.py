import os
import signal
import subprocess
import sys
from hashlib import sha256
from collections.abc import Iterable
from dataclasses import replace as replace_field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from testfixtures import LogCapture, Replacer, ShouldRaise, TempDir, compare

from chimera import __main__ as chimera_main
from chimera.addresses import Actor
from chimera.agents import AgentSession
from chimera.agents.claude import Claude
from chimera.agents.registry import AgentSpec
from chimera.archive import Archive, ArchiveSession, Event
from chimera.commands.agent import (
    agent,
    agents,
    in_goal,
    live,
    occupants,
    reconcile,
    refuse_occupied,
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
    as_session,
    capture_launches,
    context_sources,
    full_capture,
    launched,
    launching,
    SESSION_ID,
    sources_lines,
)


def _project_obj(directory: Path) -> Project:
    return Project(directory, ProjectConfig(kind='project', repo=Path('/r')))


def _agent_at(cwd: Path, name: str = 'a') -> AgentSession:
    return AgentSession(name, name, 'idle', cwd, None)


def _stub(replace: Replacer, sessions: Iterable[AgentSession] = ()) -> list[object]:
    replace.on_class(Claude.live, lambda self, cwd=None: list(sessions))
    return capture_launches(replace)


def test_agent_runs_claude_in_the_foreground_by_default(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    agent(worktree, 'proj@goal@agent')
    expected = [
        'claude',
        '--session-id',
        SESSION_ID,
        '--name',
        'proj@goal@agent',
    ]  # no bypass flag unless dangerous
    compare(calls, expected=[(expected, worktree)])


def test_agent_makes_bypass_reachable_when_dangerous(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    agent(worktree, 'proj@goal@agent', dangerous=True)
    expected = [
        'claude',
        '--session-id',
        SESSION_ID,
        '--name',
        'proj@goal@agent',
        '--allow-dangerously-skip-permissions',
    ]
    compare(calls, expected=[(expected, worktree)])


def test_agent_runs_in_the_background_when_given_a_prompt(
    tmpdir: TempDir, replace: Replacer
) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    agent(worktree, 'proj@goal@agent', 'fix the bug')
    expected = ['claude', '--bg', '--name', 'proj@goal@agent', 'fix the bug']
    compare(calls, expected=[(expected, worktree)])


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
    compare(calls, expected=[(expected, worktree)])


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
    expected = [
        'claude',
        '--session-id',
        SESSION_ID,
        '--name',
        'proj@goal@agent',
        '--model',
        'opus',
    ]
    compare(calls, expected=[(expected, worktree)])


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
    expected = [
        'claude',
        '--session-id',
        SESSION_ID,
        '--name',
        'proj@goal@agent',
        '--allow-dangerously-skip-permissions',
    ]
    compare(calls, expected=[(expected, worktree)])


def test_resume_runs_claude_resume_in_the_foreground_by_default(
    tmpdir: TempDir, replace: Replacer
) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    resume(worktree, 'proj@goal@agent')
    expected = ['claude', '--resume', 'proj@goal@agent']  # no bypass flag unless dangerous
    compare(calls, expected=[(expected, worktree)])


def test_resume_records_no_launch_for_a_passer_by_to_claim(
    tmpdir: TempDir, replace: Replacer
) -> None:
    # a resume takes nothing new — the address is already on the session's own row. An
    # unconsumed launch record is not inert: it waits out its window for whatever cold
    # starts in the directory next, handing that stranger this agent's address and mail
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    worktree = tmpdir.makedir('ws/proj/worktrees/g@agent')
    _stub(replace)
    resume(worktree, 'proj@g@agent')
    with Archive.open(ws / 'state' / 'archive.db') as store:
        assert store.claim_launch('claude', worktree, now=datetime.now(timezone.utc)) is None


def test_resume_makes_bypass_reachable_when_dangerous(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    resume(worktree, 'proj@goal@agent', dangerous=True)
    expected = ['claude', '--resume', 'proj@goal@agent', '--allow-dangerously-skip-permissions']
    compare(calls, expected=[(expected, worktree)])


def test_resume_runs_in_the_background_when_given_a_prompt(
    tmpdir: TempDir, replace: Replacer
) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    resume(worktree, 'proj@goal@agent', 'carry on')
    expected = ['claude', '--bg', '--resume', 'proj@goal@agent', 'carry on']
    compare(calls, expected=[(expected, worktree)])


def test_resume_passes_extra_flags_through(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    resume(worktree, 'proj@goal@agent', extra=['--dangerously-skip-permissions'])
    expected = ['claude', '--resume', 'proj@goal@agent', '--dangerously-skip-permissions']
    compare(calls, expected=[(expected, worktree)])


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
    compare(calls, expected=[(expected, worktree)])


_CANONICAL = '<the address this session was launched under>'
"""Sentinel: record the canonical address rather than an explicit (or absent) one."""


def _address_archived(
    workspace: Path,
    native_id: str,
    project: str = 'myproject',
    address: str | None = _CANONICAL,
) -> None:
    """A recorded session holding the ``<project>@g@agent`` address, unless told otherwise.

    The address is what chimera stamped at launch and is immutable; the registry's
    display name is a separate, mutable thing this row does not carry at all — which is
    the point of resolving a resume through here.
    """
    with Archive.open(workspace / 'state' / 'archive.db') as store:
        store.record_session(
            ArchiveSession(
                platform='claude',
                native_id=native_id,
                status='other',
                started_at=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
                address=str(Actor(project, 'g', 'agent')) if address is _CANONICAL else address,
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
    _address_archived(ws, 'uuid-renamed')  # whatever the registry now calls it
    assert resume_target(ws, 'claude', 'myproject', 'g', 'agent') == 'uuid-renamed'


def test_resume_target_ignores_an_unaddressed_session_sharing_the_worktree(
    tmpdir: TempDir, replace: Replacer
) -> None:
    # a raw `claude` or a one-shot errand records the same axes; reviving a human's
    # private conversation under the agent's name is what an address prevents
    ws = tmpdir.makedir('ws')
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    _address_archived(ws, 'uuid-agent')
    _address_archived(ws, 'uuid-a-human-opened-this', address=None)  # hand-launched
    compare(resume_target(ws, 'claude', 'myproject', 'g', 'agent'), expected='uuid-agent')


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
    claude_cmd = [
        'claude',
        '--session-id',
        SESSION_ID,
        '--name',
        'myproject@g@agent',
    ]  # no bypass flag by default
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
            middle=[launched(claude_cmd, expected)],
        ),
    )
    compare(calls, expected=[(claude_cmd, expected)])


def test_agent_start_cli_dangerous_makes_bypass_reachable(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _project_with_worktree(tmpdir)
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    claude_cmd = [
        'claude',
        '--session-id',
        SESSION_ID,
        '--name',
        'myproject@g@agent',
        '--allow-dangerously-skip-permissions',
    ]
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
            middle=[launched(claude_cmd, expected)],
        ),
    )
    compare(calls, expected=[(claude_cmd, expected)])


def test_agent_start_cli_with_prompt(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    _project_with_worktree(tmpdir)
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    claude_cmd = ['claude', '--bg', '--name', 'myproject@g@agent', 'do it']
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
            middle=[launched(claude_cmd, expected)],
        ),
    )
    compare(calls, expected=[(claude_cmd, expected)])


def test_agent_start_cli_with_actor(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    project = _project_with_worktree(tmpdir)
    (project / 'worktrees' / 'g@reviewer').mkdir()
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@reviewer'
    claude_cmd = ['claude', '--session-id', SESSION_ID, '--name', 'myproject@g@reviewer']
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
            middle=[launched(claude_cmd, expected)],
        ),
    )
    compare(calls, expected=[(claude_cmd, expected)])


def test_agent_start_cli_forwards_flags_after_dashdash(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _project_with_worktree(tmpdir)
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    # no prompt, only passthrough: the flag must not be mistaken for the prompt
    claude_cmd = [
        'claude',
        '--session-id',
        SESSION_ID,
        '--name',
        'myproject@g@agent',
        '--dangerously-skip-permissions',
    ]
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
            middle=[launched(claude_cmd, expected)],
        ),
    )
    compare(calls, expected=[(claude_cmd, expected)])


def test_agent_start_cli_with_prompt_and_passthrough(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _project_with_worktree(tmpdir)
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    claude_cmd = ['claude', '--bg', '--name', 'myproject@g@agent', '--model', 'opus', 'do it']
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
            middle=[launched(claude_cmd, expected)],
        ),
    )
    compare(calls, expected=[(claude_cmd, expected)])


def test_agent_resume_cli(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    _project_with_worktree(tmpdir)
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    claude_cmd = ['claude', '--resume', 'myproject@g@agent']  # no bypass flag by default
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
            middle=[launched(claude_cmd, expected)],
        ),
    )
    compare(calls, expected=[(claude_cmd, expected)])


def test_agent_resume_cli_with_passthrough(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _project_with_worktree(tmpdir)
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    claude_cmd = ['claude', '--resume', 'myproject@g@agent', '--dangerously-skip-permissions']
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
            middle=[launched(claude_cmd, expected)],
        ),
    )
    compare(calls, expected=[(claude_cmd, expected)])


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
    _address_archived(ws, 'uuid-1234', project='proj')
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    digest = sha256(AGENT_ROLE_TEXT.encode()).hexdigest()
    context = ws / 'state' / 'context' / f'proj@g@agent-{digest[:8]}.md'
    claude_cmd = [
        'claude',
        '--resume',
        'uuid-1234',
        '--name',
        'proj@g@agent',
        '--append-system-prompt-file',
        str(context),
    ]
    command.run('agent', 'resume', '-g', 'g').check(
        output=f'Resumed agent in {expected}',
        logging=[
            {
                'level': 'INFO',
                'command': 'agent resume',
                'goal': 'g',
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
                'goal': 'g',
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
            # no `agent: launching` — a resume records no launch to be claimed
            {**launched(claude_cmd, expected), 'goal': 'g'},
            {'level': 'INFO', 'command': 'agent resume', 'goal': 'g', 'phase': 'end'},
        ],
    )
    compare(calls, expected=[(claude_cmd, expected)])


def test_stop_is_keyed_by_worktree_so_a_rename_cannot_hide_a_session(
    tmpdir: TempDir, replace: Replacer
) -> None:
    # stop never selects by name: liveness and pids come from the registry by cwd,
    # so a session renamed in the UI is still found and stopped
    worktree = tmpdir.makedir('wt')
    pid = _orphan_sleeper()
    renamed = AgentSession('uuid-1', 'renamed in the UI', 'idle', worktree, None, pid=pid)
    replace.on_class(Claude.live, lambda self, cwd=None: [renamed] if cwd == worktree else [])
    try:
        # stop() itself proves the kill: it waits for the pid to die and raises otherwise
        compare(stop(worktree), expected=[renamed])
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_agents_aggregates_registered_harnesses(replace: Replacer) -> None:
    # the sole registered harness today is claude
    lonely = AgentSession(id='lonely', name='lonely', status='working', cwd=Path('.'), summary=None)
    replace.on_class(Claude.sessions, lambda self: [lonely])
    compare(agents(), expected=[lonely])


def _ghost_at(cwd: Path, name: str = 'ghost') -> AgentSession:
    return AgentSession(name, name, 'idle', cwd, None, stale='claimed pid 999 is not running')


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


def test_extra_bypass_flags_refused_for_a_recorded_session_alone(
    tmpdir: TempDir, replace: Replacer
) -> None:
    # no CLAUDECODE (conftest clears it): the archive knowing this process to be a
    # session it recorded is enough on its own
    as_session(tmpdir, replace, 'proj@@manager')
    worktree = tmpdir.makedir('wt')
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
    expected = [
        'claude',
        '--session-id',
        SESSION_ID,
        '--name',
        'n',
        '--allow-dangerously-skip-permissions',
    ]
    compare(calls, expected=[(expected, worktree)])


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
            AgentSession(  # a full-UUID id renders as its 8-char short form
                id='aaa11111-9f80-4c8e-b3d7-1234567890ab',
                name='proj@g@agent',
                status='busy',
                cwd=worktree,
                summary='fix it',
            ),
            AgentSession(
                id='bbb22222', name='other', status='idle', cwd=worktree, summary='do a thing'
            ),
            AgentSession(id='ccc', name='ccc', status='idle', cwd=worktree, summary='unnamed'),
            AgentSession(
                id='ddd', name='stray', status='idle', cwd=tmpdir / 'outside', summary='x'
            ),
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
        lambda: [AgentSession(id='aaa', name='named', status='busy', cwd=worktree, summary=detail)],
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
            AgentSession(
                id='aaa', name='proj@g@agent', status='busy', cwd=worktree, summary='fix it'
            ),
            AgentSession(
                id='ddd', name='stray', status='idle', cwd=tmpdir / 'outside', summary='x'
            ),
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
            AgentSession(
                id='aaa', name='proj@g@agent', status='busy', cwd=worktree, summary='fix it'
            ),
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
            AgentSession(
                id='aaa', name='proj@g@agent', status='busy', cwd=worktree, summary='fix it'
            ),
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
            AgentSession(
                id='aaa', name='proj@g@agent', status='busy', cwd=worktree, summary='fix it'
            ),
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
    expected = [
        'claude',
        '--session-id',
        SESSION_ID,
        '--name',
        'proj@goal@agent',
        '--model',
        'opus',
    ]
    compare(calls, expected=[(expected, worktree)])


def test_agent_passthrough_model_beats_spec_model(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    agent(
        worktree, 'proj@goal@agent', extra=['--model', 'sonnet'], spec=AgentSpec('claude', 'opus')
    )
    expected = [
        'claude',
        '--session-id',
        SESSION_ID,
        '--name',
        'proj@goal@agent',
        '--model',
        'sonnet',
    ]
    compare(calls, expected=[(expected, worktree)])


def test_resume_spec_model_rides_as_model_flag(tmpdir: TempDir, replace: Replacer) -> None:
    worktree = tmpdir.makedir('wt')
    calls = _stub(replace)
    resume(worktree, 'proj@goal@agent', spec=AgentSpec('claude', 'opus'))
    expected = ['claude', '--resume', 'proj@goal@agent', '--model', 'opus']
    compare(calls, expected=[(expected, worktree)])


def test_agent_start_cli_with_model_flag(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _project_with_worktree(tmpdir)
    calls = _stub(replace)
    expected = Path.cwd() / 'worktrees' / 'g@agent'
    claude_cmd = [
        'claude',
        '--session-id',
        SESSION_ID,
        '--name',
        'myproject@g@agent',
        '--model',
        'opus',
    ]
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
            middle=[launched(claude_cmd, expected)],
        ),
    )
    compare(calls, expected=[(claude_cmd, expected)])


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
    claude_cmd = [
        'claude',
        '--session-id',
        SESSION_ID,
        '--name',
        'myproject@g@agent',
        '--model',
        'sonnet',
    ]
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
            middle=[launched(claude_cmd, expected)],
        ),
    )
    compare(calls, expected=[(claude_cmd, expected)])


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
    claude_cmd = [
        'claude',
        '--session-id',
        SESSION_ID,
        '--name',
        'proj@g@agent',
        '--model',
        'ws-model',
        '--append-system-prompt-file',
        str(context),
    ]
    command.run('agent', 'start', '-g', 'g').check(
        output=f'Launched agent in {expected}',
        logging=[
            {
                'level': 'INFO',
                'command': 'agent start',
                'goal': 'g',
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
                'goal': 'g',
                'session': 'proj@g@agent',
                'path': str(context),
                'sha256': digest,
                'sources': context_sources(ws, 'agent', pinned=project.resolve()),
                'message': 'context: rendered',
            },
            {**launching(claude_cmd, expected), 'goal': 'g'},
            {**launched(claude_cmd, expected), 'goal': 'g'},
            {'level': 'INFO', 'command': 'agent start', 'goal': 'g', 'phase': 'end'},
        ],
    )
    compare(calls, expected=[(claude_cmd, expected)])


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
        '--session-id',
        SESSION_ID,
        '--name',
        'proj@goal@agent',
        '--append-system-prompt-file',
        str(tmpdir / 'ctx.md'),
    ]
    compare(calls, expected=[(expected, worktree)])


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
    claude_cmd = [
        'claude',
        '--session-id',
        SESSION_ID,
        '--name',
        'proj@g@agent',
        '--append-system-prompt-file',
        str(context),
    ]
    command.run('agent', 'start', '-g', 'g').check(
        output=f'Launched agent in {expected_wt}',
        logging=[
            {
                'level': 'INFO',
                'command': 'agent start',
                'goal': 'g',
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
                'goal': 'g',
                'session': 'proj@g@agent',
                'path': str(context),
                'sha256': digest,
                'sources': sources,
                'message': 'context: rendered',
            },
            {**launching(claude_cmd, expected_wt), 'goal': 'g'},
            {**launched(claude_cmd, expected_wt), 'goal': 'g'},
            {'level': 'INFO', 'command': 'agent start', 'goal': 'g', 'phase': 'end'},
        ],
    )
    compare(context.read_text(), expected=text)
    compare(calls, expected=[(claude_cmd, expected_wt)])


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
                'address: proj@g@agent',
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
                'goal': 'g',
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
                'goal': 'g',
                'session': 'proj@g@agent',
                'path': str(context),
                'sha256': digest,
                'sources': sources,
                'message': 'context: rendered',
            },
            {'level': 'INFO', 'command': 'agent start', 'goal': 'g', 'phase': 'end'},
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
                'address: myproject@g@agent',
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


def _orphan_sleeper() -> int:
    """A sleeping process that is not our child, so SIGTERM leaves no zombie to confuse
    the exit polling in ``Agent.stop``."""
    out = subprocess.run(
        # the sleep's stdout must be redirected, or capture_output's pipe stays open
        # until it exits and this blocks for the full 60s, returning a dead pid
        ['bash', '-c', 'sleep 60 >/dev/null 2>&1 & echo $!'],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(out.stdout)


def _session_with(pid: int | None, cwd: Path, name: str = 'p@g@agent') -> AgentSession:
    return AgentSession('x', name, 'idle', cwd, None, pid=pid)


def test_stop_terminates_the_live_session(tmpdir: TempDir, replace: Replacer) -> None:
    pid = _orphan_sleeper()
    session = _session_with(pid, tmpdir.path)
    replace.on_class(Claude.live, lambda self, cwd=None: [session])
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
    replace.on_class(Claude.live, lambda self, cwd=None: [session])
    compare(stop(tmpdir.path), expected=[session])


def test_stop_refuses_a_pidless_session(tmpdir: TempDir, replace: Replacer) -> None:
    replace.on_class(Claude.live, lambda self, cwd=None: [_session_with(None, tmpdir.path)])
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
    replace.on_class(Claude.live, lambda self, cwd=None: [_session_with(proc.pid, tmpdir.path)])
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
    replace.on_class(Claude.live, lambda self, cwd=None: [session])
    compare(stop(tmpdir.path, Dry(True)), expected=[session])


def test_agent_stop_cli_dry(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    _project_with_worktree(tmpdir)
    worktree = Path.cwd() / 'worktrees' / 'g@agent'
    replace.on_class(
        Claude.live, lambda self, cwd=None: [_session_with(4242, worktree, 'myproject@g@agent')]
    )
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
    replace.on_class(Claude.live, lambda self, cwd=None: [])
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
    replace.on_class(Claude.live, lambda self, cwd=None: [_session_with(4242, tmpdir.path)])

    def deny(pid: int, sig: int) -> None:
        raise PermissionError(1, 'Operation not permitted')

    replace(target=os.kill, container=os, name='kill', replacement=deny)
    with ShouldRaise(
        UserError('p@g@agent (pid 4242) is not ours to signal — stop it by hand, then re-run')
    ):
        stop(tmpdir.path)


def test_stop_handles_the_pid_reused_by_another_user(tmpdir: TempDir, replace: Replacer) -> None:
    session = _session_with(4242, tmpdir.path)
    replace.on_class(Claude.live, lambda self, cwd=None: [session])

    def kill(pid: int, sig: int) -> None:
        if sig == 0:  # the SIGTERM freed the pid; another user's process now wears it
            raise PermissionError(1, 'Operation not permitted')

    replace(target=os.kill, container=os, name='kill', replacement=kill)
    compare(stop(tmpdir.path), expected=[session])


def test_stop_refuses_a_pid_the_kernel_has_since_reused(tmpdir: TempDir, replace: Replacer) -> None:
    # the pid is alive and the caller believes it is the session's — but the creation
    # time says a different process now wears it. Signalling is unrecoverable, so this
    # refuses rather than SIGTERMing whatever innocent process inherited the slot.
    pid = _orphan_sleeper()
    session = replace_field(_session_with(pid, tmpdir.path), create_time=1.0)
    replace.on_class(Claude.live, lambda self, cwd=None: [session])
    try:
        with ShouldRaise(
            UserError(
                f'p@g@agent (pid {pid}) is no longer the process it named — '
                f'the pid has been reused; re-check what is live, then re-run'
            )
        ):
            stop(tmpdir.path)
        os.kill(pid, 0)  # still running: the refusal signalled nothing
    finally:
        os.kill(pid, signal.SIGKILL)


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


class TestReconcile:
    # a session that dies without its end hook firing — killed, crashed, rebooted —
    # stays open forever, and an open row outranks the closed ones a resume picks between

    def _archived(self, ws: Path, native_id: str, ended: bool = False) -> None:
        with Archive.open(ws / 'state' / 'archive.db') as store:
            store.record_session(
                ArchiveSession(
                    platform='claude',
                    native_id=native_id,
                    status='startup',
                    started_at=datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc),
                    ended_at=datetime(2026, 6, 15, 13, 0, tzinfo=timezone.utc) if ended else None,
                )
            )

    def _rows(self, ws: Path) -> dict[str, str]:
        with Archive.open(ws / 'state' / 'archive.db') as store:
            return {s.native_id: s.status for s in store.sessions()}

    def test_an_unavailable_harness_closes_nothing(
        self, tmpdir: TempDir, replace: Replacer, full_logs: LogCapture
    ) -> None:
        # `claude` off the PATH answers with an empty registry, which is indistinguishable
        # from an empty machine — closing every open row on that would have a read-only
        # lister declare every agent dead
        ws = tmpdir.makedir('ws')
        self._archived(ws, 'alive')
        replace.in_environ('PATH', '')
        compare(reconcile(ws, []), expected=[])
        compare(self._rows(ws), expected={'alive': 'startup'})  # untouched
        full_logs.check_present(
            {
                'level': 'WARNING',
                'harnesses': ['claude'],
                'message': 'agent: harness unavailable, leaving open sessions alone',
            }
        )

    def test_closes_a_session_no_harness_reports(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('ws')
        self._archived(ws, 'gone')
        closed = reconcile(ws, [])
        compare([s.native_id for s in closed], expected=['gone'])
        compare(self._rows(ws), expected={'gone': 'reconciled'})

    def test_leaves_a_session_still_reported_live(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('ws')
        self._archived(ws, 'running')
        compare(reconcile(ws, [_agent_at(ws, 'running')]), expected=[])
        compare(self._rows(ws), expected={'running': 'startup'})

    def test_leaves_a_session_already_closed(self, tmpdir: TempDir) -> None:
        # its own end hook fired; the reason it recorded must not be overwritten
        ws = tmpdir.makedir('ws')
        self._archived(ws, 'done', ended=True)
        compare(reconcile(ws, []), expected=[])
        compare(self._rows(ws), expected={'done': 'startup'})

    def test_records_an_end_event_so_the_timeline_says_how(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('ws')
        self._archived(ws, 'gone')
        reconcile(ws, [])
        with Archive.open(ws / 'state' / 'archive.db') as store:
            [event] = [e for e in store.events(native_id='gone') if e.kind == 'end']
        compare(event.detail, expected='reconciled')

    def test_says_what_it_closed(self, tmpdir: TempDir, full_logs: LogCapture) -> None:
        ws = tmpdir.makedir('ws')
        self._archived(ws, 'gone')
        reconcile(ws, [])
        full_logs.check_present(
            {
                'level': 'INFO',
                'message': 'agent: closed sessions no harness still reports',
                'sessions': ['gone'],
            }
        )


class TestOccupants:
    # "live here" and "would clash with me here" are different questions, and the gap is
    # made of things a harness reports as sessions that nobody would call an occupant

    def _workspace(self, tmpdir: TempDir, replace: Replacer) -> Path:
        tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
        ws = tmpdir.path / 'ws'
        replace.in_environ('CHIMERA_WORKSPACE', str(ws))
        return ws

    def _archived(self, ws: Path, native_id: str, cwd: Path, hour: int = 12, **kw: Any) -> None:
        with Archive.open(ws / 'state' / 'archive.db') as store:
            store.record_session(
                ArchiveSession(
                    platform='claude',
                    native_id=native_id,
                    status='startup',
                    started_at=datetime(2026, 6, 15, hour, 0, tzinfo=timezone.utc),
                    cwd=cwd,
                    workspace=ws.name,
                    **kw,
                )
            )

    def _branch(self, ws: Path, native_id: str, hour: int = 13) -> None:
        with Archive.open(ws / 'state' / 'archive.db') as store:
            store.record_event(
                Event(
                    at=datetime(2026, 6, 15, hour, 0, tzinfo=timezone.utc),
                    kind='branched',
                    platform='claude',
                    native_id=native_id,
                )
            )

    def test_a_working_session_occupies(self, tmpdir: TempDir, replace: Replacer) -> None:
        ws = self._workspace(tmpdir, replace)
        wt = tmpdir.makedir('ws/wt')
        self._archived(ws, 'chat', wt)
        replace.on_class(Claude.live, lambda self, cwd=None: [_agent_at(wt, 'chat')])
        compare([s.id for s in occupants(wt)], expected=['chat'])

    def test_a_session_the_archive_never_saw_still_occupies(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        # anything unrecognised counts: refusing beside a harmless session is
        # recoverable, a second writer in one worktree is not
        self._workspace(tmpdir, replace)
        wt = tmpdir.makedir('ws/wt')
        replace.on_class(Claude.live, lambda self, cwd=None: [_agent_at(wt, 'unknown')])
        compare([s.id for s in occupants(wt)], expected=['unknown'])

    def test_a_non_conversation_does_not_occupy(self, tmpdir: TempDir, replace: Replacer) -> None:
        # a browser draft and a one-shot -p run share the worktree with the real thing
        ws = self._workspace(tmpdir, replace)
        wt = tmpdir.makedir('ws/wt')
        self._archived(ws, 'draft', wt, addressable=False)
        replace.on_class(Claude.live, lambda self, cwd=None: [_agent_at(wt, 'draft')])
        compare(occupants(wt), expected=[])

    def test_a_husk_does_not_occupy(self, tmpdir: TempDir, replace: Replacer) -> None:
        # backgrounding leaves the parent registry-live but conversationally frozen, for
        # as long as its terminal wrapper lives — up to 35 hours observed
        ws = self._workspace(tmpdir, replace)
        wt = tmpdir.makedir('ws/wt')
        self._archived(ws, 'husk', wt)
        self._archived(ws, 'fork', wt)
        self._branch(ws, 'fork')
        replace.on_class(
            Claude.live, lambda self, cwd=None: [_agent_at(wt, 'husk'), _agent_at(wt, 'fork')]
        )
        compare([s.id for s in occupants(wt)], expected=['fork'])  # the fork is the live one

    def test_a_dead_forks_husk_never_excuses_a_later_session(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        # the guard's whole point: one backgrounded chat, long since over, must not leave
        # the worktree permanently unguarded for every session that follows
        ws = self._workspace(tmpdir, replace)
        wt = tmpdir.makedir('ws/wt')
        ended = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)
        self._archived(ws, 'husk', wt, ended_at=ended)
        self._archived(ws, 'fork', wt, hour=13, ended_at=ended)
        self._branch(ws, 'fork')
        self._archived(ws, 'later', wt, hour=15)
        replace.on_class(Claude.live, lambda self, cwd=None: [_agent_at(wt, 'later')])
        compare([s.id for s in occupants(wt)], expected=['later'])

    def test_a_session_started_after_the_fork_is_not_its_husk(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        # the husk is the fork's own presumed parent — the newest session open when it
        # forked — never simply the newest session in the directory now
        ws = self._workspace(tmpdir, replace)
        wt = tmpdir.makedir('ws/wt')
        self._archived(ws, 'husk', wt)
        self._archived(ws, 'fork', wt, hour=13)
        self._branch(ws, 'fork')
        self._archived(ws, 'newcomer', wt, hour=14)
        replace.on_class(
            Claude.live,
            lambda self, cwd=None: [
                _agent_at(wt, 'husk'),
                _agent_at(wt, 'fork'),
                _agent_at(wt, 'newcomer'),
            ],
        )
        compare(sorted(s.id for s in occupants(wt)), expected=['fork', 'newcomer'])

    def test_a_session_never_finds_itself(self, tmpdir: TempDir, replace: Replacer) -> None:
        ws = self._workspace(tmpdir, replace)
        wt = tmpdir.makedir('ws/wt')
        self._archived(ws, 'me', wt)
        replace.on_class(Claude.live, lambda self, cwd=None: [_agent_at(wt, 'me')])
        compare(occupants(wt, excluding='me'), expected=[])

    def test_outside_a_workspace_every_live_session_counts(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        # no archive to consult, so nothing can be ruled out — the safe way to be wrong
        wt = tmpdir.makedir('loose')
        replace.on_class(Claude.live, lambda self, cwd=None: [_agent_at(wt, 'x')])
        compare([s.id for s in occupants(wt)], expected=['x'])

    def test_an_empty_worktree_asks_the_archive_nothing(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        replace.on_class(Claude.live, lambda self, cwd=None: [])
        compare(occupants(tmpdir.makedir('wt')), expected=[])


class TestRefuseOccupied:
    def test_a_preview_is_never_blocked_by_liveness(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        # a preview mutates nothing, so a live session must not make it unavailable
        wt = tmpdir.makedir('wt')
        replace.on_class(Claude.live, lambda self, cwd=None: [_agent_at(wt, 'busy')])
        refuse_occupied(wt, Dry(on=True))

    def test_a_missing_worktree_is_left_to_the_launch(self, tmpdir: TempDir) -> None:
        # nothing can occupy a directory that isn't there; the launch says the definite
        # thing, and the registry is never asked
        refuse_occupied(tmpdir.path / 'ghost')
