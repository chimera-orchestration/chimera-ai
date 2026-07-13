from pathlib import Path

from chimera.commands.msg.store import mail
from chimera.comms import Message


def inbox(workspace: Path, address: str, *, unread_only: bool) -> list[Message]:
    """The messages awaiting ``address``, oldest first — undrained plus (by default) undisposed."""
    return mail(workspace).inbox(address, unread_only=unread_only)
