import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from testfixtures import Replacer, ShouldRaise, TempDir, compare
from testfixtures.mock import Mock

from chimera.commands.msg.dispose import dispose
from chimera.commands.msg.drain import drain
from chimera.commands.msg.store import mail
from chimera.commands.msg.watch import _alive, armed, line, markers, watch
from chimera.comms import Message
from tests.cli import Command, action_logs, full_capture

NOON = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
WATCH = 'chimera.commands.msg.watch.watch'
ADDRESS = 'p@g@agent'
M1_FIELDS = {  # what _seed(ws, 'm1') binds on every store/watch log line
    'msg_id': 'm1',
    'sender': 'p@manager',
    'to': ADDRESS,
    'kind': 'message',
    'priority': 'normal',
    'thread': None,
    're': None,
    'severity': None,
    'subject': 'ping',
    'body': '.',
}


class Enough(Exception):
    """Raised by a test's sleep to end the (otherwise infinite) watch."""


def _seed(ws: Path, id: str, subject: str = 'ping') -> None:
    mail(ws).send(
        Message(
            id=id,
            sender='p@manager',
            to=ADDRESS,
            kind='message',
            subject=subject,
            body='.',
            ts=NOON,
        )
    )


def _watch(ws: Path, *between_polls, once: bool = False) -> list[str]:
    """The ids a watch emits, running each callable between polls, then ending."""
    steps = iter(between_polls)

    def sleep(interval: float) -> None:
        step = next(steps, None)
        if step is None:
            raise Enough()
        step()

    emitted: list[str] = []
    with ShouldRaise(Enough):
        for message in watch(ws, ADDRESS, interval=0, once=once, sleep=sleep):
            emitted.append(message.id)
    return emitted


def test_emits_each_new_message_exactly_once(tmpdir: TempDir) -> None:
    ws = tmpdir.path
    _seed(ws, 'm0')  # already in the inbox when the watch starts — the silent baseline
    compare(
        _watch(
            ws,
            lambda: _seed(ws, 'm1'),
            lambda: drain(ws, ADDRESS),  # m1 moves new/ → cur/ — a state change, not news
            lambda: _seed(ws, 'm2'),
        ),
        expected=['m1', 'm2'],
    )


def test_notices_a_message_that_arrives_already_claimed(tmpdir: TempDir) -> None:
    ws = tmpdir.path

    def arrive_and_claim() -> None:
        _seed(ws, 'm1')
        drain(ws, ADDRESS)  # claimed before the watch ever polled it — still news, once

    compare(_watch(ws, arrive_and_claim, lambda: None), expected=['m1'])


def test_an_acked_message_is_not_reannounced(tmpdir: TempDir) -> None:
    ws = tmpdir.path
    compare(
        _watch(ws, lambda: _seed(ws, 'm1'), lambda: dispose(ws, ADDRESS, 'm1'), lambda: None),
        expected=['m1'],
    )


def test_never_claims(tmpdir: TempDir) -> None:
    ws = tmpdir.path
    compare(_watch(ws, lambda: _seed(ws, 'm1')), expected=['m1'])
    assert [m.id for m in mail(ws).inbox(ADDRESS, unread_only=True)] == ['m1']  # still in new/


def test_once_returns_after_the_first_poll_with_mail_without_a_further_poll(
    tmpdir: TempDir,
) -> None:
    ws = tmpdir.path
    polls = iter([lambda: (_seed(ws, 'm1'), _seed(ws, 'm2'))])  # both land before the poll

    def sleep(interval: float) -> None:
        next(polls)()  # a second poll would StopIteration — proof `once` didn't stop

    emitted = [m.id for m in watch(ws, ADDRESS, interval=0, once=True, sleep=sleep)]
    compare(emitted, expected=['m1', 'm2'])  # everything undelivered, then a clean return


def test_once_exits_immediately_on_mail_already_waiting(tmpdir: TempDir) -> None:
    # the arm-window race: mail landing mid-turn (after the delivery hook ran) is already
    # in new/ when the wake turn re-arms — a baseline would swallow it and the session
    # would idle deaf; undelivered mail must trigger even when it predates the watcher
    ws = tmpdir.path
    _seed(ws, 'm0')
    sleep = Mock()
    emitted = [m.id for m in watch(ws, ADDRESS, interval=0, once=True, sleep=sleep)]
    compare(emitted, expected=['m0'])
    assert sleep.call_count == 0  # exited on what was waiting — never polled at all
    assert [m.id for m in mail(ws).inbox(ADDRESS, unread_only=True)] == ['m0']  # never claims


def test_once_does_not_trigger_on_drained_unacked_mail(tmpdir: TempDir) -> None:
    # cur/ is drained-but-unacked: the delivery ledger re-surfaces it at every session's
    # next turn already, so waking on it would loop wake → deliver → re-arm → wake
    ws = tmpdir.path
    _seed(ws, 'm0')
    drain(ws, ADDRESS)
    compare(_watch(ws, once=True), expected=[])  # one quiet poll, then Enough ends it


def test_line_carries_id_parties_kind_and_subject(tmpdir: TempDir) -> None:
    _seed(tmpdir.path, 'm1', subject='build red')
    [message] = mail(tmpdir.path).inbox(ADDRESS)
    compare(line(message), expected='m1  p@manager → p@g@agent  [message] build red')


def test_an_arrival_is_the_logged_outcome(tmpdir: TempDir) -> None:
    ws = tmpdir.path
    with full_capture() as log:
        compare(_watch(ws, lambda: _seed(ws, 'm1')), expected=['m1'])
    log.check(
        {
            'level': 'INFO',
            'message': 'comms: send p@manager -> p@g@agent [message] ping (m1)',
            **M1_FIELDS,
        },
        {
            'level': 'INFO',
            'message': 'comms: watch p@manager -> p@g@agent [message] ping (m1)',
            **M1_FIELDS,
        },
    )


def test_quiet_polls_log_nothing_at_all(tmpdir: TempDir) -> None:
    ws = tmpdir.path
    _seed(ws, 'm0')  # the baseline arrives before the capture starts
    with full_capture() as log:
        compare(_watch(ws, lambda: None, lambda: None), expected=[])
    log.check()  # three polls, not a line — even DEBUG would flood at watch frequency


def test_cli_streams_lines_until_interrupted(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    _seed(ws, 'm0')  # baseline: never printed
    polls = iter([lambda: _seed(ws, 'm1'), None])

    def sleep(interval: float) -> None:
        step = next(polls)
        if step is None:
            raise KeyboardInterrupt()  # Ctrl-C — how a real watch ends
        step()

    replace(target=time.sleep, container=time, name='sleep', replacement=sleep)
    start, end = action_logs(
        'msg watch', WATCH, {'address': ADDRESS, 'interval': 5.0, 'once': False}
    )
    sent = {  # m1's mid-watch send — the watch itself logs no moves, only its emission
        'level': 'INFO',
        'message': 'comms: send p@manager -> p@g@agent [message] ping (m1)',
        **M1_FIELDS,
    }
    watched = {
        'level': 'INFO',
        'message': 'comms: watch p@manager -> p@g@agent [message] ping (m1)',
        **M1_FIELDS,
    }
    command.run('msg', 'watch', ADDRESS, '--interval', '5').check(
        output='m1  p@manager → p@g@agent  [message] ping', logging=[start, sent, watched, end]
    )


def test_cli_once_prints_one_line_then_exits(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    polls = iter([lambda: _seed(ws, 'm1')])  # m1 lands on the first poll; --once stops there

    def sleep(interval: float) -> None:
        next(polls)()

    replace(target=time.sleep, container=time, name='sleep', replacement=sleep)
    start, end = action_logs(
        'msg watch', WATCH, {'address': ADDRESS, 'interval': 0.0, 'once': True}
    )
    sent = {
        'level': 'INFO',
        'message': 'comms: send p@manager -> p@g@agent [message] ping (m1)',
        **M1_FIELDS,
    }
    watched = {
        'level': 'INFO',
        'message': 'comms: watch p@manager -> p@g@agent [message] ping (m1)',
        **M1_FIELDS,
    }
    command.run('msg', 'watch', ADDRESS, '--interval', '0', '--once').check(
        output='m1  p@manager → p@g@agent  [message] ping', logging=[start, sent, watched, end]
    )


def test_holds_the_armed_marker_while_watching(tmpdir: TempDir) -> None:
    ws = tmpdir.path
    marker = markers(ws, ADDRESS) / str(os.getpid())
    held: list[bool] = []
    compare(_watch(ws, lambda: held.append(marker.exists())), expected=[])
    compare(held, expected=[True])
    assert not marker.exists()  # the finally swept it — even an interrupted watch


def test_once_sweeps_its_marker_on_the_clean_exit(tmpdir: TempDir) -> None:
    ws = tmpdir.path
    _seed(ws, 'm0')
    list(watch(ws, ADDRESS, interval=0, once=True, sleep=Mock()))
    assert not (markers(ws, ADDRESS) / str(os.getpid())).exists()


class TestArmed:
    def test_no_marker_dir_means_unarmed(self, tmpdir: TempDir) -> None:
        assert not armed(tmpdir.path, ADDRESS, alive=Mock(side_effect=AssertionError))

    def test_a_live_marker_arms(self, tmpdir: TempDir) -> None:
        markers(tmpdir.path, ADDRESS).mkdir(parents=True)
        (markers(tmpdir.path, ADDRESS) / '123').touch()
        assert armed(tmpdir.path, ADDRESS, alive=lambda pid: pid == 123)

    def test_a_dead_marker_is_pruned_and_logged(self, tmpdir: TempDir) -> None:
        marker = markers(tmpdir.path, ADDRESS) / '123'
        marker.parent.mkdir(parents=True)
        marker.touch()
        with full_capture() as log:
            assert not armed(tmpdir.path, ADDRESS, alive=lambda pid: False)
        assert not marker.exists()
        log.check(
            {'level': 'INFO', 'message': 'msg watch: pruned stale marker', 'marker': str(marker)}
        )

    def test_a_malformed_marker_is_pruned_without_a_probe(self, tmpdir: TempDir) -> None:
        marker = markers(tmpdir.path, ADDRESS) / 'junk'
        marker.parent.mkdir(parents=True)
        marker.touch()
        assert not armed(tmpdir.path, ADDRESS, alive=Mock(side_effect=AssertionError))
        assert not marker.exists()

    def test_one_live_watcher_arms_while_the_dead_are_swept(self, tmpdir: TempDir) -> None:
        directory = markers(tmpdir.path, ADDRESS)
        directory.mkdir(parents=True)
        for pid in ('1', '2', '3'):
            (directory / pid).touch()
        assert armed(tmpdir.path, ADDRESS, alive=lambda pid: pid == 2)
        compare(sorted(m.name for m in directory.iterdir()), expected=['2'])


class TestAlive:
    def test_dead_pid(self, replace: Replacer) -> None:
        def kill(pid: int, sig: int) -> None:
            raise ProcessLookupError()

        replace(target=os.kill, container=os, name='kill', replacement=kill)
        assert not _alive(123)

    def test_another_users_pid_cannot_be_our_watcher(self, replace: Replacer) -> None:
        def kill(pid: int, sig: int) -> None:
            raise PermissionError()

        replace(target=os.kill, container=os, name='kill', replacement=kill)
        assert not _alive(123)

    def test_a_recycled_pid_running_something_else(self) -> None:
        # this very pytest process: alive, but its argv is no `msg watch`
        assert not _alive(os.getpid())

    def test_a_running_watch(self, replace: Replacer) -> None:
        def run(args: list[str], capture_output: bool, text: bool) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(args, 0, stdout='ch msg watch p@g@agent --once\n')

        replace(target=os.kill, container=os, name='kill', replacement=lambda pid, sig: None)
        replace(target=subprocess.run, container=subprocess, name='run', replacement=run)
        assert _alive(123)
