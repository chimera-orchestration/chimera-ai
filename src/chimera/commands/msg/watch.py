"""``ch msg watch`` — the wake-up feed: one line per message newly appearing in an inbox.

Strictly read-only: it never claims (drains), marks or moves a message, so any number of
watchers run beside the delivery hook without stealing its mail. Run it under a harness
monitor so a new line wakes an idle session — one message per line, flushed as it lands,
is the contract. The inbox (``new/`` + ``cur/``) is polled each ``interval`` seconds: a
mailbox is two small directories, so this stays cheap without a filesystem-watch
dependency, and a message another process drains mid-watch is still noticed (and never
repeated — ids are remembered, not states).

An arrival is the outcome, so each emitted message lands one INFO line (``comms: watch``,
via :func:`chimera.comms.log_action`); a quiet poll logs nothing at all — a watch polls
every few seconds forever, so even a DEBUG line per poll would bury the log.
"""

import time
from collections.abc import Callable, Iterator
from pathlib import Path

from chimera.commands.msg.store import mail
from chimera.comms import Message, log_action


def line(message: Message) -> str:
    """The one-line rendering: id, sender, recipient, [kind] subject."""
    return f'{message.id}  {message.sender} → {message.to}  [{message.kind}] {message.subject}'


def watch(
    workspace: Path,
    address: str,
    *,
    interval: float,
    sleep: Callable[[float], None] | None = None,
) -> Iterator[Message]:
    """Yield each message newly appearing in ``address``'s inbox, oldest first, forever.

    Messages already in the inbox when the watch starts are the baseline — never yielded;
    every later arrival is yielded exactly once. ``sleep`` (default ``time.sleep``) is the
    injectable pause between polls.
    """
    box = mail(workspace)
    seen = {message.id for message in box.inbox(address)}
    while True:
        for message in box.inbox(address):
            if message.id not in seen:
                seen.add(message.id)
                log_action('watch', message)
                yield message
        (sleep or time.sleep)(interval)
