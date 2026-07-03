import re
from pathlib import Path

from giterator import Git

from chimera.commands.project.track import register, track

_URL = re.compile(r'://|^[^/]+@[^/]+:')


def add(workspace: Path, source: str) -> Path:
    """Add a project: bare-clone source if it's a URL, else track it as a local checkout.

    A bare clone keeps ``repo/`` free of a checked-out working tree — all work happens in
    goal worktrees, never in ``repo/`` itself, and a checked-out tree there only confuses
    git GUIs into treating it as a working checkout alongside its own worktrees. The fetch
    refspec, initial fetch and ``origin/HEAD`` symref are set up by hand since ``git clone
    --bare`` skips them, so ``default_branch``/``base_ref`` see the same ``origin/<default>``
    remote-tracking ref a normal clone would give them for free.
    """
    if _URL.search(source):
        name = re.split(r'[/:]', source.rstrip('/'))[-1].removesuffix('.git')
        repo = workspace / name / 'repo'
        Git(workspace)('clone', '--bare', source, str(repo))
        git = Git(repo)
        git('config', 'remote.origin.fetch', '+refs/heads/*:refs/remotes/origin/*')
        git('fetch', '--prune', 'origin')
        git('remote', 'set-head', 'origin', '-a')
        return register(workspace, name, repo)
    return track(workspace, Path(source))
