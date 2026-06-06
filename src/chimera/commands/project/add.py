import re
from pathlib import Path

from giterator import Git

from chimera.commands.project.track import register, track

_URL = re.compile(r'://|^[^/]+@[^/]+:')


def add(workspace: Path, source: str) -> Path:
    """Add a project: clone source if it's a URL, else track it as a local checkout."""
    if _URL.search(source):
        name = re.split(r'[/:]', source.rstrip('/'))[-1].removesuffix('.git')
        repo = workspace / name / 'repo'
        Git(workspace)('clone', source, str(repo))
        return register(workspace, name, repo)
    return track(workspace, Path(source))
