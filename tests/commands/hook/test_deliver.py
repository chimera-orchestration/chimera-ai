import io
import json
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from testfixtures import Replacer, TempDir

from chimera.agents.claude import Claude
from chimera.commands.hook.capture import session_start
from chimera.commands.hook.deliver import deliver
from chimera.commands.msg.dispose import dispose
from chimera.commands.msg.drain import drain
from chimera.archive import Archive, ArchiveSession
from chimera.commands.msg.store import mail
from chimera.comms import Message
from tests.cli import Command, Run, action_logs

NOON = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
DELIVER = 'chimera.commands.hook.deliver.deliver'
ADDRESS = 'p@g@agent'


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


def _ws(tmpdir: TempDir, replace: Replacer, *, addressed: Sequence[str] = ()) -> Path:
    """A workspace where each of ``addressed`` holds ADDRESS — the only way mail reaches one.

    Several sessions can hold one address over its life (each relaunch is a new session),
    which is exactly what the per-session ``seen/`` ledger is for.
    """
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    with Archive.open(ws / 'state' / 'archive.db') as store:
        for native_id in addressed or ('uuid-1',):
            store.record_session(
                ArchiveSession(
                    platform='claude',
                    native_id=native_id,
                    status='startup',
                    started_at=NOON,
                    address=ADDRESS,
                    cwd=ws,
                )
            )
    return ws


def test_deliver_claims_and_surfaces_new_mail(tmpdir: TempDir, replace: Replacer) -> None:
    ws = _ws(tmpdir, replace)
    _seed(ws, 'm1')
    assert [m.id for m in deliver(ws, 'uuid-1')] == ['m1']
    assert mail(ws).inbox(ADDRESS, unread_only=True) == []  # claimed: new/ → cur/


def test_deliver_surfaces_mail_a_third_party_drained(tmpdir: TempDir, replace: Replacer) -> None:
    ws = _ws(tmpdir, replace)
    _seed(ws, 'm1')
    drain(ws, ADDRESS)  # another process claims it — the hook must not go silent
    assert [m.id for m in deliver(ws, 'uuid-1')] == ['m1']


def test_deliver_does_not_respam_a_session_but_stops_only_at_ack(
    tmpdir: TempDir, replace: Replacer
) -> None:
    ws = _ws(tmpdir, replace, addressed=('uuid-1', 'uuid-2', 'uuid-3'))
    _seed(ws, 'm1')
    deliver(ws, 'uuid-1')
    assert deliver(ws, 'uuid-1') == []  # this session already saw it
    assert [m.id for m in deliver(ws, 'uuid-2')] == ['m1']  # unacked → a fresh session still does
    dispose(ws, ADDRESS, 'm1')
    assert deliver(ws, 'uuid-3') == []  # acked → surfacing over


def test_deliver_skips_a_session_archived_without_an_address(
    tmpdir: TempDir, replace: Replacer
) -> None:
    # a one-shot -p run shares its cwd with real conversations, so the gate is the
    # archive's own record that this session holds no address
    ws = _ws(tmpdir, replace, addressed=('uuid-chat',))
    session_start(
        Claude(),
        {
            'cwd': str(ws),
            'session_id': 'uuid-p',
            'transcript_path': '/t/uuid-p.jsonl',
            'source': 'startup',
        },
        {'CLAUDE_CODE_ENTRYPOINT': 'sdk-cli'},
    )
    _seed(ws, 'm1')
    assert deliver(ws, 'uuid-p') == []
    assert [m.id for m in deliver(ws, 'uuid-chat')] == ['m1']  # the real session still gets it


def test_deliver_outside_a_workspace_is_a_noop(tmpdir: TempDir, replace: Replacer) -> None:
    replace.in_environ('CHIMERA_SESSION', ADDRESS)
    assert deliver(tmpdir.path / 'nowhere', 'uuid-x') == []
    assert not (tmpdir.path / 'nowhere').exists()  # nothing written either


def _run_hook(command: Command, replace: Replacer, ws: Path, session: str) -> Run:
    payload = json.dumps({'cwd': str(ws), 'session_id': session})
    replace(target=sys.stdin, container=sys, name='stdin', replacement=io.StringIO(payload))
    return command.run('hook', 'deliver')


def test_hook_deliver_cli_injects_a_block(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    ws = _ws(tmpdir, replace)
    _seed(ws, 'm1')
    drain(ws, ADDRESS)  # even a third-party claim doesn't silence the injection
    start, end = action_logs('hook deliver', DELIVER, {})
    delivered = {
        'level': 'INFO',
        'message': 'comms: deliver p@manager -> p@g@agent [message] ping (m1)',
        'session': 'uuid-1',
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
    _run_hook(command, replace, ws, 'uuid-1').check(
        output=(
            'You have inter-agent mail; once a message is handled, `ch msg ack <id>` it:\n'
            '- m1 from p@manager [message] ping: .'
        ),
        logging=[start, delivered, end],
    )


def test_hook_deliver_cli_is_silent_once_seen(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    ws = _ws(tmpdir, replace, addressed=('uuid-1', 'uuid-2', 'uuid-3'))
    _seed(ws, 'm1')
    deliver(ws, 'uuid-1')
    _run_hook(command, replace, ws, 'uuid-1').check(
        output='', logging=action_logs('hook deliver', DELIVER, {})
    )
