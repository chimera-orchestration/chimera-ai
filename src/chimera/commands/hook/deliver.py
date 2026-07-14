"""Mail delivery at the turn boundary: the UserPromptSubmit hook.

The injection must surface every unacked message, not just what this call claims itself:
``ch msg drain`` moves mail ``new/`` → ``cur/`` for whoever runs it, so an inject path
that printed only its own claims would go permanently silent about a message some other
process drained — the delivery trap. :meth:`chimera.comms.Comms.deliver` holds the
invariant instead: until acked, a message reaches every session of its recipient, each
exactly once (the per-session ``seen/`` ledger); only ``ch msg ack``/``defer`` end it.

Whether the session *has* an address is the archive's fact, not re-inferred here: a
session its SessionStart recorded without one (a one-shot ``claude -p`` — see
``capture.addressed``) shares a cwd with real conversations, so ``caller(cwd)`` would
happily hand it their mail. A session the archive has no row for still gets its mail —
a real chat must not go silent because a hook misfired.
"""

from pathlib import Path

from loguru import logger

from chimera.commands.hook.capture import archive
from chimera.commands.msg.store import mail
from chimera.comms import Message
from chimera.config import NotInWorkspaceError
from chimera.context import caller, resolve_workspace


def deliver(cwd: Path, session: str) -> list[Message]:
    """The unacked messages for ``cwd``'s address not yet seen by ``session``, claiming them.

    The hook fires for every session on the machine; outside any workspace there is no
    mailbox to read, and a session archived without a mail address gets none — those are
    the no-ops.
    """
    try:
        workspace = resolve_workspace(cwd)
        address = caller(cwd)
    except NotInWorkspaceError:
        return []
    with archive(workspace) as store:
        recorded = store.session('claude', session)
    if recorded is not None and recorded.name is None:
        logger.bind(session=session).info('hook deliver: unaddressed session, no mail')
        return []
    return mail(workspace).deliver(address, session)
