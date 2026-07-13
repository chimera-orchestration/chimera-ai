from pathlib import Path

from chimera.comms import Comms, Message


def outstanding(workspace: Path) -> list[tuple[str, Message]]:
    """Every message in the workspace's mailboxes, oldest first, as ``(state, message)``.

    The mail store lives at ``<workspace>/state/mail``; ``state`` is ``new``/``cur``/``done``.
    """
    return Comms(workspace / 'state' / 'mail').messages()
