import shutil
from pathlib import Path

from giterator import Git

TEMPLATE = Path(__file__).parent.parent / 'templates' / 'workspace'


def init(path: Path) -> Path:
    """Initialize a new workspace at path, returning the path."""
    if path.exists():
        raise FileExistsError(path)
    shutil.copytree(TEMPLATE, path)
    Git(path).init()
    return path
