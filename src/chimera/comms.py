"""The comms store: inter-agent mail, one immutable message per file.

Where the archive (``chimera.archive``) is a queryable *lens* over what happened,
comms is *operational* state — the system of record for messages in flight between
actors (captain, managers, goal agents). So it is deliberately **not** the archive's
SQLite: a mailbox wants atomic delivery and single-consumer claim, which a Maildir
gives lockless and dependency-free — it runs even with no archive, and any number of
agents can send at once without a writer lock (the honest fit for chimera's
*Independence* principle). Nothing here is wired into a command yet.

Layout — one mailbox per address, four states, transitions are atomic renames::

    <root>/<address>/
        tmp/    # being written (rename into new/ on completion)
        new/    # delivered, never yet drained
        cur/    # drained (injected ≥once), awaiting disposition
        done/   # disposed — handled, deferred or expired

Addresses are the ontology chimera already mints — ``pegasus``,
``<project>@manager``, ``<project>@<goal>@<actor>`` — so a mailbox name *is* a
session name. Filenames are the message id, which sorts chronologically, so a plain
directory listing is FIFO.

The store models the message *lifecycle*; *why* a message was disposed (handled vs
deferred, and the deferral reason) is audit metadata for the log, not the mailbox —
the same log-is-truth split the archive keeps. Each send, receive (drain) and disposal
logs at INFO through :func:`_log`: routing in the message text (who -> whom, kind,
subject, id — what a live tail shows) and :meth:`Message.log_fields` bound structured —
so a message's whole journey is traceable from the log alone; the read-only
peeks (inbox/thread) stay silent. Timestamps must be timezone-aware.
"""

import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict

Kind = Literal['message', 'request', 'escalation', 'notice']
"""``message`` = FYI with a reply welcome; ``request`` expects one (blocks a Stop hook
while undisposed); ``escalation`` carries ``severity`` and routes upward; ``notice`` is
fire-and-forget (never nags, may carry ``expires``)."""

Priority = Literal['normal', 'urgent']

_STATES = ('tmp', 'new', 'cur', 'done')


class Message(BaseModel):
    """One inter-agent message. Immutable once sent; its lifecycle is which dir it sits in.

    ``sender``/``to`` are addresses (``<project>@<goal>@<actor>`` & friends). ``thread``
    is the id of the conversation's root message (``None`` on a root); ``re`` the id
    replied to. ``severity`` rides ``escalation`` kinds; ``expires`` a ``notice``.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    sender: str
    to: str
    kind: Kind
    subject: str
    body: str
    ts: datetime
    priority: Priority = 'normal'
    thread: str | None = None
    re: str | None = None
    severity: int | None = None
    expires: datetime | None = None
    v: int = 1

    def log_fields(self) -> dict[str, object]:
        """The canonical structured fields every log site binds for an action on this message.

        Source, destination, the identifiers of all parties and the message, and the content —
        so a message's whole journey is reconstructable from the log alone and no site (send,
        drain, dispose, delivery) can forget a field. The store's own sites bind them via
        :func:`_log`; anywhere else, ``logger.bind(**msg.log_fields())``.
        """
        return {
            'msg_id': self.id,
            'sender': self.sender,
            'to': self.to,
            'kind': self.kind,
            'priority': self.priority,
            'thread': self.thread,
            're': self.re,
            'severity': self.severity,
            'subject': self.subject,
            'body': self.body,
        }


def compose(
    *,
    sender: str,
    to: str,
    kind: Kind,
    subject: str,
    body: str,
    priority: Priority = 'normal',
    thread: str | None = None,
    re: str | None = None,
    severity: int | None = None,
    expires: datetime | None = None,
    now: datetime | None = None,
) -> Message:
    """Build a :class:`Message` with a fresh, chronologically-sortable id and timestamp."""
    ts = now if now is not None else datetime.now(timezone.utc)
    return Message(
        id=_new_id(ts),
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


class Comms:
    """A handle on the comms tree rooted at ``root`` (typically ``<workspace>/comms``)."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def send(self, message: Message) -> Message:
        """Deliver ``message`` to its recipient's mailbox; a re-send of the same id is a no-op.

        Writes to ``tmp/`` then atomically renames into ``new/`` — so a reader never sees a
        half-written file, and concurrent senders can't collide. Returns ``message`` for chaining.
        """
        mailbox = self._ensure(message.to)
        name = f'{message.id}.json'
        if any((mailbox / state / name).exists() for state in ('new', 'cur', 'done')):
            return message  # already delivered — idempotent
        tmp = mailbox / 'tmp' / name
        tmp.write_text(message.model_dump_json())
        os.replace(tmp, mailbox / 'new' / name)
        _log('send', message)
        return message

    def inbox(self, address: str, *, unread_only: bool = False) -> list[Message]:
        """The messages awaiting ``address``, oldest first: undrained plus (by default) undisposed."""
        states = ('new',) if unread_only else ('new', 'cur')
        return self._collect(address, states)

    def drain(self, address: str) -> list[Message]:
        """Claim every undrained message for ``address`` (``new/`` → ``cur/``), returning them.

        The rename *is* the claim: if two drainers race, each message is claimed exactly once
        (the loser's rename fails and is skipped). This is what the delivery hook calls per turn.
        """
        new_dir = self._root / address / 'new'
        if not new_dir.is_dir():
            return []
        cur_dir = self._ensure(address) / 'cur'
        claimed: list[Message] = []
        for path in sorted(new_dir.glob('*.json'), key=lambda p: p.name):
            destination = cur_dir / path.name
            try:
                os.replace(path, destination)
            except FileNotFoundError:
                continue  # another drainer claimed it first
            message = _read(destination)
            _log('receive', message)
            claimed.append(message)
        return claimed

    def dispose(self, address: str, message_id: str) -> None:
        """Retire a message (``new/`` or ``cur/`` → ``done/``). No-op if already gone (idempotent).

        Both ``ch msg ack`` and ``ch msg defer`` land here — the store records only *that* a
        message was disposed; *how*, and any deferral reason, is the caller's to log.
        """
        mailbox = self._root / address
        name = f'{message_id}.json'
        done = self._ensure(address) / 'done'
        for state in ('cur', 'new'):
            source = mailbox / state / name
            if source.exists():
                message = _read(source)
                os.replace(source, done / name)
                _log('dispose', message)
                return

    def thread(self, address: str, thread: str) -> list[Message]:
        """Every message of a conversation in ``address``'s mailbox, oldest first, across states.

        ``thread`` is the root message's id; a message belongs to it when it *is* the root or
        carries the root as its ``thread``.
        """
        return [
            message
            for message in self._collect(address, ('new', 'cur', 'done'))
            if thread in (message.id, message.thread)
        ]

    def messages(self) -> list[tuple[str, Message]]:
        """Every message across all mailboxes, oldest first, as ``(state, message)``.

        ``state`` is ``new`` (delivered, undrained), ``cur`` (drained, awaiting disposition)
        or ``done`` (disposed, awaiting cleanup) — the whole picture, for a captain to inspect.
        """
        found: list[tuple[str, Message]] = []
        if not self._root.is_dir():
            return found
        for mailbox in sorted(self._root.iterdir()):
            for state in ('new', 'cur', 'done'):
                found.extend((state, _read(path)) for path in (mailbox / state).glob('*.json'))
        return sorted(found, key=lambda item: item[1].id)

    def _ensure(self, address: str) -> Path:
        mailbox = self._root / address
        for state in _STATES:
            (mailbox / state).mkdir(parents=True, exist_ok=True)
        return mailbox

    def _collect(self, address: str, states: tuple[str, ...]) -> list[Message]:
        paths: list[Path] = []
        for state in states:
            directory = self._root / address / state
            if directory.is_dir():
                paths.extend(directory.glob('*.json'))
        return [_read(path) for path in sorted(paths, key=lambda p: p.name)]


def _log(action: str, message: Message) -> None:
    """Land ``action`` on ``message`` at INFO — the one log site for every mailbox mutation.

    The text carries the routing (``comms: send <sender> -> <to> [<kind>] <subject> (<id>)``)
    because that's all a live tail shows; :meth:`Message.log_fields` rides bound, so the
    structured record stays whole. One helper, so no site can drift from either half.
    """
    logger.bind(**message.log_fields()).info(
        f'comms: {action} {message.sender} -> {message.to} '
        f'[{message.kind}] {message.subject} ({message.id})'
    )


def _new_id(ts: datetime) -> str:
    """A chronologically-sortable, collision-free message id (also the filename stem)."""
    return f'{ts.astimezone(timezone.utc):%Y%m%dT%H%M%S%f}-{secrets.token_hex(4)}'


def _read(path: Path) -> Message:
    return Message.model_validate_json(path.read_text())
