import time
from datetime import datetime, timezone
from pathlib import Path

from testfixtures import Replacer, ShouldRaise, TempDir, compare

from chimera.commands.msg.dispose import dispose
from chimera.commands.msg.drain import drain
from chimera.commands.msg.store import mail
from chimera.commands.msg.watch import line, watch
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


def _watch(ws: Path, *between_polls) -> list[str]:
    """The ids a watch emits, running each callable between polls, then ending."""
    steps = iter(between_polls)

    def sleep(interval: float) -> None:
        step = next(steps, None)
        if step is None:
            raise Enough()
        step()

    emitted: list[str] = []
    with ShouldRaise(Enough):
        for message in watch(ws, ADDRESS, interval=0, sleep=sleep):
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


def test_once_returns_after_the_first_arrival_without_a_further_poll(tmpdir: TempDir) -> None:
    ws = tmpdir.path
    polls = iter([lambda: (_seed(ws, 'm1'), _seed(ws, 'm2'))])  # both land before the poll

    def sleep(interval: float) -> None:
        next(polls)()  # a second poll would StopIteration — proof `once` didn't stop

    emitted = [m.id for m in watch(ws, ADDRESS, interval=0, once=True, sleep=sleep)]
    compare(emitted, expected=['m1'])  # only the first, and it returned cleanly


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
