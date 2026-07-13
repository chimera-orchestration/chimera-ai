import os
from datetime import datetime, timedelta, timezone
from threading import Barrier, Thread

from testfixtures import LogCapture, Replacer, TempDir
from testfixtures.loguru import LoguruSource

from chimera.comms import Comms, Kind, Message, Priority, compose
from tests.cli import full_capture

NOON = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
TO = 'chimera@logs-and-sessions@agent'
FROM = 'chimera@manager'


def a_message(
    id: str = 'm1',
    *,
    to: str = TO,
    sender: str = FROM,
    kind: Kind = 'message',
    subject: str = 'hello',
    body: str = 'a body',
    ts: datetime = NOON,
    priority: Priority = 'normal',
    thread: str | None = None,
    re: str | None = None,
    severity: int | None = None,
    expires: datetime | None = None,
) -> Message:
    return Message(
        id=id,
        sender=sender,
        to=to,
        kind=kind,
        subject=subject,
        body=body,
        ts=ts,
        priority=priority,
        thread=thread,
        re=re,
        severity=severity,
        expires=expires,
    )


def test_compose_generates_a_sortable_id_and_timestamp() -> None:
    message = compose(sender=FROM, to=TO, kind='message', subject='hi', body='...', now=NOON)
    assert message.id.startswith('20260712T120000')
    assert message.ts == NOON


def test_compose_defaults_the_timestamp_to_now() -> None:
    assert (
        compose(sender=FROM, to=TO, kind='notice', subject='hi', body='...').ts.tzinfo is not None
    )


def test_log_fields_carry_source_destination_parties_and_content() -> None:
    message = a_message(
        'e1',
        kind='escalation',
        priority='urgent',
        subject='build is red',
        thread='r1',
        re='r1',
        severity=1,
    )
    assert message.log_fields() == {
        'msg_id': 'e1',
        'sender': FROM,
        'to': TO,
        'kind': 'escalation',
        'priority': 'urgent',
        'thread': 'r1',
        're': 'r1',
        'severity': 1,
        'subject': 'build is red',
        'body': 'a body',
    }


def test_a_sent_message_lands_in_the_recipients_inbox(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    comms.send(a_message('m1'))
    assert comms.inbox(TO) == [a_message('m1')]


def test_send_delivers_a_fully_populated_message_intact(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    escalation = a_message(
        'e1',
        kind='escalation',
        priority='urgent',
        subject='build is red',
        thread='root-1',
        re='root-1',
        severity=1,
        expires=NOON + timedelta(hours=1),
    )
    comms.send(escalation)
    assert comms.inbox(TO) == [escalation]  # every field roundtrips, incl. datetimes and severity


def test_send_is_idempotent_on_the_same_id(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    comms.send(a_message('m1'))
    comms.send(a_message('m1'))
    assert [m.id for m in comms.inbox(TO)] == ['m1']  # delivered once, not twice


def test_re_sending_an_already_drained_message_is_a_no_op(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    comms.send(a_message('m1'))
    comms.drain(TO)  # now in cur/
    comms.send(a_message('m1'))
    assert comms.inbox(TO, unread_only=True) == []  # not re-delivered to new/
    assert [m.id for m in comms.inbox(TO)] == ['m1']


def test_re_sending_an_already_disposed_message_is_a_no_op(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    comms.send(a_message('m1'))
    comms.dispose(TO, 'm1')
    comms.send(a_message('m1'))
    assert comms.inbox(TO) == []  # stays disposed, not resurrected


def test_inbox_is_empty_for_an_unused_address(tmpdir: TempDir) -> None:
    assert Comms(tmpdir.path).inbox('nobody@nowhere@agent') == []


def test_unread_only_excludes_already_drained_messages(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    comms.send(a_message('01'))
    comms.send(a_message('02'))
    comms.drain(TO)  # 01, 02 → cur/
    comms.send(a_message('03'))
    assert [m.id for m in comms.inbox(TO, unread_only=True)] == ['03']
    assert [m.id for m in comms.inbox(TO)] == ['01', '02', '03']


def test_messages_come_back_oldest_first(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    comms.send(a_message('02'))
    comms.send(a_message('01'))
    assert [m.id for m in comms.inbox(TO)] == ['01', '02']


def test_drain_claims_new_messages_then_has_nothing_left(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    comms.send(a_message('01'))
    comms.send(a_message('02'))
    assert [m.id for m in comms.drain(TO)] == ['01', '02']
    assert comms.drain(TO) == []  # already claimed, nothing new
    assert [m.id for m in comms.inbox(TO)] == ['01', '02']  # still awaiting disposition in cur/


def test_drain_is_empty_for_an_unused_address(tmpdir: TempDir) -> None:
    assert Comms(tmpdir.path).drain('nobody@nowhere@agent') == []


def test_dispose_removes_a_drained_message_from_the_inbox(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    comms.send(a_message('m1'))
    comms.drain(TO)
    comms.dispose(TO, 'm1')
    assert comms.inbox(TO) == []


def test_dispose_works_on_an_undrained_message(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    comms.send(a_message('m1'))
    comms.dispose(TO, 'm1')  # straight from new/, never drained
    assert comms.inbox(TO) == []


def test_dispose_is_a_no_op_for_an_unknown_message(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    comms.send(a_message('m1'))
    comms.dispose(TO, 'never-sent')
    assert [m.id for m in comms.inbox(TO)] == ['m1']  # the real one is untouched


def test_thread_gathers_the_conversation_across_states(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    comms.send(a_message('01', subject='root'))
    comms.send(a_message('02', subject='reply', thread='01', re='01'))
    comms.send(a_message('03', subject='unrelated'))
    comms.drain(TO)
    comms.dispose(TO, '01')  # root disposed, reply still in cur — thread spans both states
    assert [m.subject for m in comms.thread(TO, '01')] == ['root', 'reply']


def test_drain_skips_a_message_another_drainer_already_claimed(
    tmpdir: TempDir, replace: Replacer
) -> None:
    comms = Comms(tmpdir.path)
    comms.send(a_message('01'))

    def already_gone(*_: object) -> None:
        raise FileNotFoundError  # the losing side of a concurrent claim

    replace(target=os.replace, container=os, name='replace', replacement=already_gone)
    assert comms.drain(TO) == []
    assert [m.id for m in comms.inbox(TO, unread_only=True)] == ['01']  # left for the winner


def test_messages_lists_every_mailbox_with_its_state(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    comms.send(a_message('01', to='a@g@agent', subject='to-a'))
    comms.send(a_message('02', to='b@manager', subject='to-b'))
    comms.drain('a@g@agent')  # 01 → cur/
    assert [(state, m.subject) for state, m in comms.messages()] == [
        ('cur', 'to-a'),
        ('new', 'to-b'),
    ]


def test_messages_is_empty_for_an_unused_root(tmpdir: TempDir) -> None:
    assert Comms(tmpdir.path / 'nope').messages() == []


def _trace() -> LogCapture:
    """Capture everything, DEBUG included — the message trace is what these tests cover."""
    return LogCapture(LoguruSource(('message', 'extra')))


def test_sending_a_message_is_logged_with_full_context(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    message = a_message('m1', subject='ping')
    with _trace() as log:
        comms.send(message)
    # routing in the text (all a live tail shows), full context bound
    log.check((f'comms: send {FROM} -> {TO} [message] ping (m1)', message.log_fields()))


def test_receiving_a_message_is_logged(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    message = a_message('m1')
    comms.send(message)  # outside the capture, so only the receive is caught
    with _trace() as log:
        comms.drain(TO)
    log.check((f'comms: receive {FROM} -> {TO} [message] hello (m1)', message.log_fields()))


def test_disposing_a_message_is_logged(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    message = a_message('m1')
    comms.send(message)
    with _trace() as log:
        comms.dispose(TO, 'm1')
    log.check((f'comms: dispose {FROM} -> {TO} [message] hello (m1)', message.log_fields()))


def test_a_long_subject_is_elided_in_the_text_never_the_fields(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    message = a_message('m1', subject='s' * 80)
    with _trace() as log:
        comms.send(message)
    log.check((f'comms: send {FROM} -> {TO} [message] {"s" * 57}... (m1)', message.log_fields()))


def test_the_logged_text_carries_the_kind_and_subject(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    message = a_message('e1', kind='escalation', priority='urgent', subject='build is red')
    with _trace() as log:
        comms.send(message)
    log.check((f'comms: send {FROM} -> {TO} [escalation] build is red (e1)', message.log_fields()))


def test_the_read_only_peeks_trace_at_debug(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    comms.send(a_message('m1'))
    with full_capture() as log:
        comms.inbox(TO)
        comms.thread(TO, 'm1')
        comms.messages()
    log.check(
        {
            'level': 'DEBUG',
            'message': f'comms: inbox {TO} (1)',
            'address': TO,
            'unread_only': False,
            'count': 1,
        },
        {
            'level': 'DEBUG',
            'message': f'comms: thread m1 {TO} (1)',
            'address': TO,
            'thread': 'm1',
            'count': 1,
        },
        {'level': 'DEBUG', 'message': 'comms: messages (1)', 'count': 1},
    )


def test_many_agents_send_to_one_mailbox_concurrently(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    count = 12
    ready = Barrier(count)

    def sender(n: int) -> None:
        ready.wait()  # overlap the writes
        comms.send(a_message(f'{n:02d}', subject=f'from {n}'))

    threads = [Thread(target=sender, args=(n,)) for n in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(comms.inbox(TO)) == count  # every send landed, none corrupted


def test_concurrent_drainers_each_claim_a_message_once(tmpdir: TempDir) -> None:
    comms = Comms(tmpdir.path)
    for n in range(20):
        comms.send(a_message(f'{n:02d}'))
    claimed: list[list[str]] = []
    ready = Barrier(3)

    def drainer() -> None:
        ready.wait()
        claimed.append([m.id for m in comms.drain(TO)])

    threads = [Thread(target=drainer) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    everyone = sorted(mid for batch in claimed for mid in batch)
    assert everyone == [f'{n:02d}' for n in range(20)]  # each message claimed exactly once
