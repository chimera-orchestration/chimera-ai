from pathlib import Path

from chimera.comms import Comms


def mail(workspace: Path) -> Comms:
    """The workspace's mail store, rooted at ``state/mail``."""
    return Comms(workspace / 'state' / 'mail')
