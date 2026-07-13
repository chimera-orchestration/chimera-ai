from pathlib import Path

from chimera.commands.msg.store import mail
from chimera.comms import Message


def thread(workspace: Path, address: str, root: str) -> list[Message]:
    """The whole conversation ``root`` in ``address``'s mailbox, oldest first, across states."""
    return mail(workspace).thread(address, root)
