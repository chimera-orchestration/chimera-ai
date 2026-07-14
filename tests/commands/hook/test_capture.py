import io
import sys
from pathlib import Path

import pytest
from testfixtures import Replacer, TempDir, compare

from chimera.agents import Session as LiveSession
from chimera.agents.claude import Claude
from chimera.archive import Archive, Event, Session
from chimera.commands.hook.capture import addressed, session_end, session_start
from tests.cli import Command, action_logs

START = 'chimera.commands.hook.capture.session_start'
END = 'chimera.commands.hook.capture.session_end'


@pytest.fixture(autouse=True)
def live_sessions(replace: Replacer) -> list[LiveSession]:
    """What the harness registry claims is live — the test's to dictate (by appending),
    never the machine's own sessions."""
    sessions: list[LiveSession] = []
    replace.on_class(Claude.live, lambda self, cwd=None: list(sessions))
    return sessions


def _occupant(cwd: Path, id: str, status: str = 'busy') -> LiveSession:
    return LiveSession(id, 'proj@g@agent', status, cwd, None, pid=42)


def _archived(ws: Path) -> list[Session]:
    with Archive.open(ws / 'state' / 'archive.db') as a:
        return a.sessions()


def _events(ws: Path, session_id: str) -> list[Event]:
    with Archive.open(ws / 'state' / 'archive.db') as a:
        return a.events(platform='claude', native_id=session_id)


def test_session_start_records_the_session(tmpdir: TempDir, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace', 'captain': 'pegasus'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_ROLE', '')  # a raw session → manager none
    session_start(ws, 'uuid-1', '/t.jsonl', 'startup')
    [session] = _archived(ws)
    assert (session.platform, session.native_id, session.manager) == ('claude', 'uuid-1', 'none')
    assert (session.name, session.status, session.workspace) == ('pegasus', 'startup', 'ws')


def test_session_start_in_a_goal_worktree_sets_the_axes(tmpdir: TempDir, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    tmpdir.dump('ws/proj/config.yaml', {'kind': 'project', 'repo': '/r'})
    worktree = tmpdir.path / 'ws' / 'proj' / 'worktrees' / 'g@agent'
    worktree.mkdir(parents=True)
    replace.in_environ('CHIMERA_WORKSPACE', str(tmpdir / 'ws'))
    session_start(worktree, 'uuid-2', '/t.jsonl', 'startup')
    [session] = _archived(tmpdir.path / 'ws')
    assert (session.project, session.goal, session.actor) == ('proj', 'g', 'agent')
    assert session.name == 'proj@g@agent'


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
    assert session.name == 'proj@g@reviewer'


def test_session_start_marks_manager_chimera_under_a_role_stamp(
    tmpdir: TempDir, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_ROLE', 'agent')
    session_start(ws, 'uuid-3', '/t.jsonl', 'startup')
    assert _archived(ws)[0].manager == 'chimera'


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
    assert session.name is None  # recorded, but no address for mail to route to
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
    assert session.name is None
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
    assert _archived(ws)[0].name is None  # both signals read: payload agent_type, env entrypoint


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


@pytest.fixture()
def goal_worktree(tmpdir: TempDir, replace: Replacer) -> Path:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    tmpdir.dump('ws/proj/config.yaml', {'kind': 'project', 'repo': '/r'})
    worktree = tmpdir.path / 'ws' / 'proj' / 'worktrees' / 'g@agent'
    worktree.mkdir(parents=True)
    replace.in_environ('CHIMERA_WORKSPACE', str(tmpdir / 'ws'))
    return worktree


def _warning(named: str) -> str:
    return (
        f'WARNING: another agent session is already live in this worktree: {named}. '
        f'Two sessions sharing one worktree write over each other and its branch ends up '
        f'with commits neither made. Do not write, commit, or continue goal work here — '
        f'surface this to the user immediately: one session must stop (`ch agent stop`, '
        f'or exit the other) before work continues.'
    )


class TestOccupied:
    def test_warns_naming_the_live_session(
        self, goal_worktree: Path, live_sessions: list[LiveSession]
    ) -> None:
        live_sessions.append(_occupant(goal_worktree, 'd60e15e8-1111-2222-3333-444444444444'))
        warning = session_start(goal_worktree, 'uuid-new', '/t.jsonl', 'resume')
        compare(warning, expected=_warning('d60e15e8 (busy)'))
        assert _archived(goal_worktree.parents[2])[0].native_id == 'uuid-new'  # still recorded

    def test_a_free_worktree_is_silent(self, goal_worktree: Path) -> None:
        assert session_start(goal_worktree, 'uuid-new', '/t.jsonl', 'resume') is None

    def test_the_starting_session_is_not_its_own_occupant(
        self, goal_worktree: Path, live_sessions: list[LiveSession]
    ) -> None:
        live_sessions.append(_occupant(goal_worktree, '7b657aac-0dce-4929-8584-de69fb61c4ce'))
        assert (
            session_start(goal_worktree, '7b657aac-0dce-4929-8584-de69fb61c4ce', '/t', 'resume')
            is None
        )

    def test_a_short_registry_id_still_matches_the_starting_session(
        self, goal_worktree: Path, live_sessions: list[LiveSession]
    ) -> None:
        # a registry entry with no full sessionId
        live_sessions.append(_occupant(goal_worktree, '7b657aac'))
        assert (
            session_start(goal_worktree, '7b657aac-0dce-4929-8584-de69fb61c4ce', '/t', 'resume')
            is None
        )

    def test_a_subagent_rides_along_without_a_warning(
        self, goal_worktree: Path, live_sessions: list[LiveSession]
    ) -> None:
        # a subagent's cwd is its parent's worktree — the parent being live there is the
        # normal state, not an occupancy
        live_sessions.append(_occupant(goal_worktree, 'd60e15e8-1111-2222-3333-444444444444'))
        assert (
            session_start(goal_worktree, 'uuid-sub', '/t', 'startup', agent_type='researcher')
            is None
        )

    def test_a_one_shot_print_run_is_never_warned(
        self, goal_worktree: Path, live_sessions: list[LiveSession]
    ) -> None:
        live_sessions.append(_occupant(goal_worktree, 'd60e15e8-1111-2222-3333-444444444444'))
        assert session_start(goal_worktree, 'uuid-p', '/t', 'startup', entrypoint='sdk-cli') is None

    def test_only_goal_worktrees_are_policed(
        self, goal_worktree: Path, live_sessions: list[LiveSession]
    ) -> None:
        # a chat deliberately sits alongside whatever its scope's cwd is running
        project = goal_worktree.parents[1]
        live_sessions.append(_occupant(project, 'd60e15e8-1111-2222-3333-444444444444'))
        assert session_start(project, 'uuid-chat', '/t', 'startup') is None


def test_hook_session_start_cli_warns_of_an_occupied_worktree(
    goal_worktree: Path, command: Command, replace: Replacer, live_sessions: list[LiveSession]
) -> None:
    live_sessions.append(_occupant(goal_worktree, 'd60e15e8-1111-2222-3333-444444444444'))
    payload = (
        f'{{"cwd": "{goal_worktree}", "session_id": "7b657aac-0dce-4929-8584-de69fb61c4ce", '
        f'"transcript_path": "/t.jsonl", "source": "resume"}}'
    )
    replace(target=sys.stdin, container=sys, name='stdin', replacement=io.StringIO(payload))
    start, end = action_logs('hook session-start', START, {})
    command.run('hook', 'session-start').check(
        output=_warning('d60e15e8 (busy)') + '\n',
        logging=[
            start,
            {
                'level': 'WARNING',
                'session_id': '7b657aac-0dce-4929-8584-de69fb61c4ce',
                'occupants': ['d60e15e8-1111-2222-3333-444444444444'],
                'cwd': str(goal_worktree),
                'message': 'hook session-start: worktree occupied by d60e15e8 (busy)',
            },
            end,
        ],
    )
