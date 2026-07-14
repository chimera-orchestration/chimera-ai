from pathlib import Path

from chimera.commands.msg.store import mail
from chimera.comms import Message


def drain(workspace: Path, address: str) -> list[Message]:
    """Claim (receive) every undrained message for ``address`` — the delivery step.

    Moves them ``new/`` → ``cur/`` (each logged ``comms: receive``), so the maildir move is
    itself the delivery record. Claiming never silences a message: until acked it keeps
    reaching the recipient's sessions through ``ch hook deliver`` — so this is safe to run
    by hand to confirm delivery reached a mailbox.
    """
    return mail(workspace).drain(address)


def as_context(messages: list[Message]) -> str:
    """Render messages as a block to inject at a turn boundary.

    Each line leads with the message id: acking (``ch msg ack <id>``) is the sole thing
    that stops a message being re-surfaced, so the block must hand the session the id.
    """
    lines = ['You have inter-agent mail; once a message is handled, `ch msg ack <id>` it:']
    lines += [f'- {m.id} from {m.sender} [{m.kind}] {m.subject}: {m.body}' for m in messages]
    return '\n'.join(lines)
