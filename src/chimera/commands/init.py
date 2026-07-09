import shutil
from pathlib import Path

from chimera.git import Git

TEMPLATE = Path(__file__).parent.parent / 'templates' / 'workspace'


def init(path: Path, captain: str | None = None) -> Path:
    """Initialize a new workspace at path, returning the path.

    ``captain`` names the workspace's captain persona (the ``captain:`` config key —
    the workspace-level agent ``ch chat`` launches); unset, the persona is just
    "captain".
    """
    if path.exists():
        raise FileExistsError(path)
    shutil.copytree(TEMPLATE, path)
    if captain is not None:
        config = path / 'config.yaml'
        config.write_text(config.read_text() + f'captain: {captain}\n')
    Git(path).init()
    return path
