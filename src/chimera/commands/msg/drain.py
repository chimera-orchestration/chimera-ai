from pathlib import Path

from chimera.commands.msg.store import mail
from chimera.comms import Message


def drain(workspace: Path, address: str) -> list[Message]:
    """Claim (receive) every undrained message for ``address`` — the delivery step.

    Moves them ``new/`` → ``cur/`` (each logged ``comms: receive``), so the maildir move is
    itself the delivery record. This is what the turn-boundary hook runs; also runnable by
    hand to confirm delivery reached a mailbox.
    """
    return mail(workspace).drain(address)


def as_context(messages: list[Message]) -> str:
    """Render claimed messages as a block to inject at a turn boundary."""
    lines = ['You have new inter-agent mail:']
    lines += [f'- from {m.sender} [{m.kind}] {m.subject}: {m.body}' for m in messages]
    return '\n'.join(lines)
