from pathlib import Path

from loguru import logger

from chimera.commands.msg.store import mail
from chimera.comms import Message


def inbox(workspace: Path, address: str, *, unread_only: bool) -> list[Message]:
    """The messages awaiting ``address``, oldest first — undrained plus (by default) undisposed.

    The one-shot peek logs its outcome — whose inbox, how much found — here: the store's
    :meth:`~chimera.comms.Comms.inbox` is silent because ``ch msg watch`` polls it.
    """
    found = mail(workspace).inbox(address, unread_only=unread_only)
    logger.bind(address=address, unread_only=unread_only, count=len(found)).info(
        f'comms: inbox {address} ({len(found)})'
    )
    return found
