import io
import sys
from pathlib import Path

from testfixtures import Replacer, TempDir, not_there

from chimera.archive import Archive, ArchiveSession, Event
from chimera.commands.hook.capture import addressed, session_end, session_start
from tests.cli import Command, action_logs

START = 'chimera.commands.hook.capture.session_start'
END = 'chimera.commands.hook.capture.session_end'


def _archived(ws: Path) -> list[ArchiveSession]:
    with Archive.open(ws / 'state' / 'archive.db') as a:
        return a.sessions()


def _events(ws: Path, session_id: str) -> list[Event]:
    with Archive.open(ws / 'state' / 'archive.db') as a:
        return a.events(platform='claude', native_id=session_id)


def test_session_start_records_the_session(tmpdir: TempDir, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace', 'captain': 'pegasus'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    session_start(ws, 'uuid-1', '/t.jsonl', 'startup')
    [session] = _archived(ws)
    assert (session.platform, session.native_id) == ('claude', 'uuid-1')
    assert (session.address, session.status, session.workspace) == ('@@captain', 'startup', 'ws')


def test_session_start_records_the_model(tmpdir: TempDir, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace', 'captain': 'pegasus'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    session_start(ws, 'uuid-1', '/t.jsonl', 'startup', model='claude-sonnet-5')
    [session] = _archived(ws)
    assert session.model == 'claude-sonnet-5'


def test_session_start_in_a_goal_worktree_sets_the_axes(tmpdir: TempDir, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    tmpdir.dump('ws/proj/config.yaml', {'kind': 'project', 'repo': '/r'})
    worktree = tmpdir.path / 'ws' / 'proj' / 'worktrees' / 'g@agent'
    worktree.mkdir(parents=True)
    replace.in_environ('CHIMERA_WORKSPACE', str(tmpdir / 'ws'))
    session_start(worktree, 'uuid-2', '/t.jsonl', 'startup')
    [session] = _archived(tmpdir.path / 'ws')
    assert (session.project, session.goal, session.actor) == ('proj', 'g', 'agent')
    assert session.address == 'proj@g@agent'


def test_session_start_in_a_reviewer_worktree_records_the_true_actor(
    tmpdir: TempDir, replace: Replacer
) -> None:
    # the actor comes from the <goal>@<actor> dir, never assumed: a reviewer session
    # archived as the agent's would hand `agent resume` the wrong conversation
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    tmpdir.dump('ws/proj/config.yaml', {'kind': 'project', 'repo': '/r'})
    worktree = tmpdir.path / 'ws' / 'proj' / 'worktrees' / 'g@reviewer'
    worktree.mkdir(parents=True)
    # the goal is known through its agent worktree (see worktrees.goals); the reviewer's
    # sits alongside, as `ch worktree add --actor` leaves things
    (tmpdir.path / 'ws' / 'proj' / 'worktrees' / 'g@agent').mkdir()
    replace.in_environ('CHIMERA_WORKSPACE', str(tmpdir / 'ws'))
    session_start(worktree, 'uuid-r', '/t.jsonl', 'startup')
    [session] = _archived(tmpdir.path / 'ws')
    assert (session.project, session.goal, session.actor) == ('proj', 'g', 'reviewer')
    assert session.address == 'proj@g@reviewer'


def test_session_start_records_the_harness_version(tmpdir: TempDir, replace: Replacer) -> None:
    # which build produced the row: a version sessions.md has never validated is the alarm
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('AI_AGENT', 'claude-code_2-1-220_agent')
    session_start(ws, 'uuid-3', '/t.jsonl', 'startup')
    assert _archived(ws)[0].harness_version == 'claude-code_2-1-220_agent'


def test_session_start_without_a_harness_version_records_none(
    tmpdir: TempDir, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('AI_AGENT', not_there)
    session_start(ws, 'uuid-4', '/t.jsonl', 'startup')
    assert _archived(ws)[0].harness_version is None


def test_session_start_outside_a_workspace_is_a_noop(tmpdir: TempDir) -> None:
    session_start(tmpdir.path / 'nowhere', 'uuid-x', '/t.jsonl', 'startup')
    assert not (tmpdir.path / 'nowhere' / 'state').exists()


def test_session_end_marks_the_session_ended(tmpdir: TempDir, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_ROLE', '')
    session_start(ws, 'uuid-1', '/t.jsonl', 'startup')
    session_end(ws, 'uuid-1', 'logout')
    [session] = _archived(ws)
    assert session.status == 'logout'
    assert session.ended_at is not None


def test_addressed_is_the_default_and_fails_open() -> None:
    assert addressed(None, None)  # older claude with no entrypoint stamp: keep the address
    assert addressed(None, 'cli')  # interactive chats and --bg agents alike
    assert not addressed('claude', 'cli')  # a `claude agents` TUI draft / subagent
    assert not addressed(None, 'sdk-cli')  # a one-shot `claude -p` run


def test_a_tui_draft_session_never_acquires_a_mail_address(
    tmpdir: TempDir, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace', 'captain': 'pegasus'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    session_start(ws, 'uuid-tui', '/t.jsonl', 'startup', agent_type='claude')
    [session] = _archived(ws)
    assert session.address is None  # recorded, but no address for mail to route to
    assert session.workspace == 'ws'  # the location facts survive


def test_a_one_shot_print_run_never_acquires_a_mail_address(
    tmpdir: TempDir, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    tmpdir.dump('ws/proj/config.yaml', {'kind': 'project', 'repo': '/r'})
    worktree = tmpdir.path / 'ws' / 'proj' / 'worktrees' / 'g@agent'
    worktree.mkdir(parents=True)
    replace.in_environ('CHIMERA_WORKSPACE', str(tmpdir / 'ws'))
    session_start(worktree, 'uuid-p', '/t.jsonl', 'startup', entrypoint='sdk-cli')
    [session] = _archived(tmpdir.path / 'ws')
    assert session.address is None
    assert session.actor is None  # live_session_for(project, goal, 'agent') can't match it
    assert (session.project, session.goal) == ('proj', 'g')  # where it ran is still on record


def test_a_resumed_session_keeps_its_row_and_gains_the_history(
    tmpdir: TempDir, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_ROLE', '')
    session_start(ws, 'uuid-1', '/t.jsonl', 'startup')
    started = _archived(ws)[0].started_at
    session_end(ws, 'uuid-1', 'other')
    session_start(ws, 'uuid-1', '/t.jsonl', 'resume')
    [session] = _archived(ws)  # one row per identity, however many lives
    assert session.started_at == started
    assert session.ended_at is None
    assert session.status == 'resume'
    timeline = [(e.kind, e.detail) for e in _events(ws, 'uuid-1')]
    assert timeline == [('startup', None), ('end', 'other'), ('resume', None)]


def test_session_end_for_a_session_the_hooks_never_saw_records_no_event(
    tmpdir: TempDir, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    session_end(ws, 'pre-hook-uuid', 'other')  # no crash — nothing to stitch an event to
    assert _archived(ws) == []
    assert _events(ws, 'pre-hook-uuid') == []


def test_session_end_outside_a_workspace_is_a_noop(tmpdir: TempDir) -> None:
    session_end(tmpdir.path / 'nowhere', 'uuid-x', 'logout')  # no crash, nothing written
    assert not (tmpdir.path / 'nowhere' / 'state').exists()


def test_hook_session_start_cli(tmpdir: TempDir, command: Command, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_ROLE', '')
    payload = (
        f'{{"cwd": "{ws}", "session_id": "uuid-1", '
        f'"transcript_path": "/t.jsonl", "source": "startup"}}'
    )
    replace(target=sys.stdin, container=sys, name='stdin', replacement=io.StringIO(payload))
    command.run('hook', 'session-start').check(
        output='', logging=action_logs('hook session-start', START, {})
    )
    assert _archived(ws)[0].native_id == 'uuid-1'


def test_hook_session_start_cli_reads_the_model_from_the_payload(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_ROLE', '')
    payload = (
        f'{{"cwd": "{ws}", "session_id": "uuid-1", "transcript_path": "/t.jsonl", '
        f'"source": "startup", "model": "claude-sonnet-5"}}'
    )
    replace(target=sys.stdin, container=sys, name='stdin', replacement=io.StringIO(payload))
    command.run('hook', 'session-start').check(
        output='', logging=action_logs('hook session-start', START, {})
    )
    assert _archived(ws)[0].model == 'claude-sonnet-5'


def test_hook_session_start_cli_fences_on_payload_and_environment(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_ROLE', '')
    replace.in_environ('CLAUDE_CODE_ENTRYPOINT', 'sdk-cli')
    payload = (
        f'{{"cwd": "{ws}", "session_id": "uuid-p", '
        f'"transcript_path": "/t.jsonl", "source": "startup", "agent_type": "claude"}}'
    )
    replace(target=sys.stdin, container=sys, name='stdin', replacement=io.StringIO(payload))
    start, end = action_logs('hook session-start', START, {})
    command.run('hook', 'session-start').check(
        output='',
        logging=[
            start,
            {
                'level': 'INFO',
                'session_id': 'uuid-p',
                'agent_type': 'claude',
                'entrypoint': 'sdk-cli',
                'message': 'hook session-start: not a conversation, recording without a mail address',
            },
            end,
        ],
    )
    assert _archived(ws)[0].address is None  # both signals read: payload agent_type, env entrypoint


def test_hook_session_end_cli(tmpdir: TempDir, command: Command, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_ROLE', '')
    session_start(ws, 'uuid-1', '/t.jsonl', 'startup')
    payload = f'{{"cwd": "{ws}", "session_id": "uuid-1", "reason": "logout"}}'
    replace(target=sys.stdin, container=sys, name='stdin', replacement=io.StringIO(payload))
    command.run('hook', 'session-end').check(
        output='', logging=action_logs('hook session-end', END, {})
    )
    assert _archived(ws)[0].status == 'logout'


def test_hook_session_start_cli_surfaces_unmodeled_payload_keys(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    # hook_event_name is known-and-ignored; parent_session_id stands in for whatever
    # the harness starts sending next — surfaced with its value, never silently dropped
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_ROLE', '')
    payload = (
        f'{{"cwd": "{ws}", "session_id": "uuid-f", "transcript_path": "/t.jsonl", '
        f'"source": "fork", "hook_event_name": "SessionStart", '
        f'"parent_session_id": "uuid-parent"}}'
    )
    replace(target=sys.stdin, container=sys, name='stdin', replacement=io.StringIO(payload))
    start, end = action_logs('hook session-start', START, {})
    command.run('hook', 'session-start').check(
        output='',
        logging=[
            start,
            {
                'level': 'INFO',
                'session_id': 'uuid-f',
                'payload': {'parent_session_id': 'uuid-parent'},
                'message': 'hook session-start: unmodeled payload keys',
            },
            end,
        ],
    )


def test_hook_session_end_cli_surfaces_unmodeled_payload_keys(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_ROLE', '')
    session_start(ws, 'uuid-1', '/t.jsonl', 'startup')
    payload = (
        f'{{"cwd": "{ws}", "session_id": "uuid-1", "reason": "other", '
        f'"hook_event_name": "SessionEnd", "bridge_session_id": "cse_abc"}}'
    )
    replace(target=sys.stdin, container=sys, name='stdin', replacement=io.StringIO(payload))
    start, end = action_logs('hook session-end', END, {})
    command.run('hook', 'session-end').check(
        output='',
        logging=[
            start,
            {
                'level': 'INFO',
                'session_id': 'uuid-1',
                'payload': {'bridge_session_id': 'cse_abc'},
                'message': 'hook session-end: unmodeled payload keys',
            },
            end,
        ],
    )
