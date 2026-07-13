import io
import sys
from pathlib import Path

from testfixtures import Replacer, TempDir

from chimera.archive import Archive, Session
from chimera.commands.hook.capture import session_end, session_start
from tests.cli import Command, action_logs

START = 'chimera.commands.hook.capture.session_start'
END = 'chimera.commands.hook.capture.session_end'


def _archived(ws: Path) -> list[Session]:
    with Archive.open(ws / 'state' / 'archive.db') as a:
        return a.sessions()


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
