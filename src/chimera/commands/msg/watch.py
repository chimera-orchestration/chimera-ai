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

**Armed is a checkable fact, not a hope.** A running watch holds a marker —
``state/watch/<address>/<pid>`` (one pidfile per watcher; several may legally watch one
address), removed in a ``finally`` so even an interrupted watch leaves no corpse — and
:func:`armed` answers "is any live watcher holding this address?" for whoever asks (the
delivery hook's re-arm reminder, the Stop hook, doctor). A marker whose pid is dead, or is
no longer a ``msg watch`` at all (pid reuse), is pruned as it is found.

An arrival is the outcome, so each emitted message lands one INFO line (``comms: watch``,
via :func:`chimera.comms.log_action`); a quiet poll logs nothing at all — a watch polls
every few seconds forever, so even a DEBUG line per poll would bury the log. Marker
create/remove is motion, unlogged; pruning a stale one is an outcome (a watcher died
without its ``finally``), one INFO each.
"""

import os
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from loguru import logger

from chimera.commands.msg.store import mail
from chimera.comms import Message, log_action


def line(message: Message) -> str:
    """The one-line rendering: id, sender, recipient, [kind] subject."""
    return f'{message.id}  {message.sender} → {message.to}  [{message.kind}] {message.subject}'


def markers(workspace: Path, address: str) -> Path:
    """The directory of ``address``'s live-watcher markers — one pidfile per watcher."""
    return workspace / 'state' / 'watch' / address


def _alive(pid: int) -> bool:
    """True when ``pid`` is a running ``msg watch`` — not merely a running process.

    ``os.kill(pid, 0)`` probes existence without signalling; the ``ps`` argv check guards
    the marker against pid reuse, which would otherwise keep a dead watcher looking armed.
    A pid owned by another user cannot be this workspace's watcher, so it counts as dead.
    """
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    probe = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True)
    return probe.returncode == 0 and 'msg watch' in probe.stdout


def armed(workspace: Path, address: str, *, alive: Callable[[int], bool] | None = None) -> bool:
    """True while any live watcher holds a marker for ``address``, pruning dead markers.

    ``alive`` (default :func:`_alive`) is the injectable liveness probe. Every marker is
    visited even once a live one is found, so a single call sweeps the whole directory.
    """
    probe = alive or _alive
    found = False
    directory = markers(workspace, address)
    if not directory.is_dir():
        return False
    for marker in directory.iterdir():
        if marker.name.isdigit() and probe(int(marker.name)):
            found = True
        else:
            marker.unlink(missing_ok=True)
            logger.bind(marker=str(marker)).info('msg watch: pruned stale marker')
    return found


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
    ``time.sleep``) is the injectable pause between polls. While running, the watch holds
    the :func:`armed` marker for ``address``.
    """
    box = mail(workspace)
    directory = markers(workspace, address)
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / str(os.getpid())
    marker.touch()
    try:
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
    finally:
        marker.unlink(missing_ok=True)
