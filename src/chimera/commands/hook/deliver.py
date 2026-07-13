"""Mail delivery at the turn boundary: the UserPromptSubmit hook.

The injection must surface every unacked message, not just what this call claims itself:
``ch msg drain`` moves mail ``new/`` → ``cur/`` for whoever runs it, so an inject path
that printed only its own claims would go permanently silent about a message some other
process drained — the delivery trap. :meth:`chimera.comms.Comms.deliver` holds the
invariant instead: until acked, a message reaches every session of its recipient, each
exactly once (the per-session ``seen/`` ledger); only ``ch msg ack``/``defer`` end it.
"""

from pathlib import Path

from chimera.commands.msg.store import caller, mail
from chimera.comms import Message
from chimera.config import NotInWorkspaceError
from chimera.context import resolve_workspace


def deliver(cwd: Path, session: str) -> list[Message]:
    """The unacked messages for ``cwd``'s address not yet seen by ``session``, claiming them.

    The hook fires for every session on the machine; outside any workspace there is no
    mailbox to read, so that is the one no-op.
    """
    try:
        workspace = resolve_workspace(cwd)
        address = caller(cwd)
    except NotInWorkspaceError:
        return []
    return mail(workspace).deliver(address, session)
