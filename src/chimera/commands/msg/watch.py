"""``ch msg watch`` — the wake-up feed: one line per message newly appearing in an inbox.

Strictly read-only: it never claims (drains), marks or moves a message, so any number of
watchers run beside the delivery hook without stealing its mail. The inbox is polled each
``interval`` seconds: a mailbox is two small directories, so this stays cheap without a
filesystem-watch dependency.

The two modes trigger on different things. The streaming default (run under a harness
monitor) reports *arrivals*: everything in the inbox (``new/`` + ``cur/``) at start is the
baseline — never emitted — and each later appearance is emitted exactly once, even one
another process drained mid-watch (ids are remembered, not states). ``once`` — the mode a
background-task wake needs, since a task notifies on process *exit* — instead triggers on
*undelivered mail existing*: anything in ``new/``, including mail already waiting when the
watcher arms. No baseline, deliberately: a watcher is re-armed at the end of a wake turn,
so a message that landed mid-turn (after the delivery hook ran) is already sitting in
``new/`` at arm time — a baseline would swallow it and leave the session deaf until some
unrelated turn. Anything in ``new/`` has by definition never been injected anywhere
(delivery's first act is the drain), so exiting on it is always right; drained-but-unacked
mail (``cur/``) never triggers ``once`` — re-surfacing that is the delivery ledger's job,
and waking on it would loop.

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
    once: bool = False,
    sleep: Callable[[float], None] | None = None,
) -> Iterator[Message]:
    """Yield messages appearing in ``address``'s inbox, oldest first.

    Streaming (the default) yields each arrival after a start-time baseline and runs
    forever; ``once`` yields whatever undelivered (``new/``) mail exists — waiting at arm
    time or landing later — then returns, so a background task's exit wakes the session
    (see the module docstring for why the modes trigger differently). ``sleep`` (default
    ``time.sleep``) is the injectable pause between polls.
    """
    box = mail(workspace)
    if once:
        while True:
            if pending := box.inbox(address, unread_only=True):
                for message in pending:
                    log_action('watch', message)
                    yield message
                return
            (sleep or time.sleep)(interval)
    seen = {message.id for message in box.inbox(address)}
    while True:
        for message in box.inbox(address):
            if message.id not in seen:
                seen.add(message.id)
                log_action('watch', message)
                yield message
        (sleep or time.sleep)(interval)
