from pathlib import Path

from chimera.commands.msg.store import mail


def dispose(workspace: Path, address: str, message_id: str) -> None:
    """Retire a message from ``address``'s mailbox — both ``ch msg ack`` and ``defer`` land here.

    The store records only *that* the message was disposed; whether it was handled or deferred
    (and any deferral reason) is carried by the action-log frame, not the mailbox.
    """
    mail(workspace).dispose(address, message_id)
