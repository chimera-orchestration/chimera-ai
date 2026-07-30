"""Mail delivery at the turn boundary: the UserPromptSubmit hook.

The injection must surface every unacked message, not just what this call claims itself:
``ch msg drain`` moves mail ``new/`` → ``cur/`` for whoever runs it, so an inject path
that printed only its own claims would go permanently silent about a message some other
process drained — the delivery trap. :meth:`chimera.comms.Comms.deliver` holds the
invariant instead: until acked, a message reaches every session of its recipient, each
exactly once (the per-session ``seen/`` ledger); only ``ch msg ack``/``defer`` end it.

**Mail goes to the session's own address, never to its seat.** The hook fires for every
session on the machine, including ones chimera never launched, and several of them share
a worktree — a one-shot print run, a browser draft, a ``claude`` you opened yourself.
Delivering to whatever address the *directory* speaks for would hand any of them an
agent's mail. A session the archive holds no address for therefore receives nothing:
there is no inbox that is rightfully its.
"""

from pathlib import Path

from loguru import logger

from chimera.archive import archive
from chimera.commands.msg.store import mail
from chimera.comms import Message
from chimera.config import NotInWorkspaceError
from chimera.context import resolve_workspace


def deliver(cwd: Path, session: str) -> list[Message]:
    """The unacked messages for ``session``'s own address, not yet seen by it, claiming them.

    The hook fires for every session on the machine; outside any workspace there is no
    mailbox to read, and a session with no address of its own gets nothing — those are
    the no-ops.
    """
    try:
        workspace = resolve_workspace(cwd)
    except NotInWorkspaceError:
        return []
    with archive(workspace) as store:
        recorded = store.session('claude', session)
    if recorded is None or recorded.address is None:
        logger.bind(session=session).info('hook deliver: unaddressed session, no mail')
        return []
    return mail(workspace).deliver(recorded.address, session)
