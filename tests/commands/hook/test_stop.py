import io
import json
import sys
from pathlib import Path

from testfixtures import Replacer, TempDir, compare

from chimera.commands.hook import stop as stop_module
from chimera.commands.hook.capture import session_start
from chimera.commands.hook.stop import stop
from chimera.commands.msg.watch import _alive, markers
from tests.cli import Command, Run, action_logs, full_capture

STOP = 'chimera.commands.hook.stop.stop'
ADDRESS = 'p@g@agent'
REASON = (
    f'No mail watcher is armed for {ADDRESS}: run `ch msg watch --once` in the '
    'background as a task, then finish — without one, mail cannot wake this session '
    'once idle.'
)


def _ws(tmpdir: TempDir, replace: Replacer) -> Path:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_SESSION', ADDRESS)
    return ws


def _arm(ws: Path, replace: Replacer) -> None:
    markers(ws, ADDRESS).mkdir(parents=True)
    (markers(ws, ADDRESS) / '123').touch()
    replace.in_module(_alive, lambda pid: True)


def test_blocks_an_unwatched_idle_once(tmpdir: TempDir, replace: Replacer) -> None:
    ws = _ws(tmpdir, replace)
    with full_capture() as log:
        compare(stop(ws, 'uuid-1'), expected=REASON)
    log.check_present(
        {
            'level': 'INFO',
            'message': 'hook stop: blocked an unwatched idle',
            'session': 'uuid-1',
            'address': ADDRESS,
        }
    )


def test_allows_while_a_watcher_holds_the_address(tmpdir: TempDir, replace: Replacer) -> None:
    ws = _ws(tmpdir, replace)
    _arm(ws, replace)
    assert stop(ws, 'uuid-1') is None


def test_a_blocked_stop_never_loops(tmpdir: TempDir, replace: Replacer) -> None:
    # the harness marks the continuation turn stop_hook_active — still unwatched, but a
    # session that won't (or can't) re-arm is let go, not trapped
    ws = _ws(tmpdir, replace)
    assert stop(ws, 'uuid-1', stop_hook_active=True) is None


def test_an_unaddressed_session_is_never_blocked(tmpdir: TempDir, replace: Replacer) -> None:
    ws = _ws(tmpdir, replace)
    replace.in_environ('CHIMERA_ROLE', '')
    session_start(ws, 'uuid-p', '/t.jsonl', 'startup', entrypoint='sdk-cli')
    assert stop(ws, 'uuid-p') is None


def test_outside_a_workspace_allows(tmpdir: TempDir, replace: Replacer) -> None:
    replace.in_environ('CHIMERA_SESSION', ADDRESS)
    assert stop(tmpdir.path / 'nowhere', 'uuid-x') is None


def test_a_broken_check_allows_rather_than_traps(tmpdir: TempDir, replace: Replacer) -> None:
    ws = _ws(tmpdir, replace)

    def broken(workspace: Path, address: str) -> bool:
        raise RuntimeError('marker dir unreadable')

    replace(target=stop_module.armed, container=stop_module, name='armed', replacement=broken)
    with full_capture() as log:
        assert stop(ws, 'uuid-1') is None
    log.check_present(
        {
            'level': 'WARNING',
            'message': 'hook stop: check failed, allowing the stop',
            'session': 'uuid-1',
            'error': "RuntimeError('marker dir unreadable')",
        }
    )


def _run_hook(command: Command, replace: Replacer, ws: Path, session: str, **extra) -> Run:
    payload = json.dumps({'cwd': str(ws), 'session_id': session, **extra})
    replace(target=sys.stdin, container=sys, name='stdin', replacement=io.StringIO(payload))
    return command.run('hook', 'stop')


def test_hook_stop_cli_emits_the_block_decision(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    ws = _ws(tmpdir, replace)
    start, end = action_logs('hook stop', STOP, {})
    blocked = {
        'level': 'INFO',
        'message': 'hook stop: blocked an unwatched idle',
        'session': 'uuid-1',
        'address': ADDRESS,
    }
    _run_hook(command, replace, ws, 'uuid-1').check(
        output=json.dumps({'decision': 'block', 'reason': REASON}),
        logging=[start, blocked, end],
    )


def test_hook_stop_cli_is_silent_on_allow(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    ws = _ws(tmpdir, replace)
    _run_hook(command, replace, ws, 'uuid-1', stop_hook_active=True).check(
        output='', logging=action_logs('hook stop', STOP, {})
    )
