import re
from pathlib import Path

from chimera.commands.project.track import register, track
from chimera.commands.worktree.add import add as worktree_add
from chimera.config import UserError
from chimera.git import Git
from chimera.worktrees import default_branch

_URL = re.compile(r'://|^[^/]+@[^/]+:')


def add(workspace: Path, source: str, checkout: Path | None = None) -> Path:
    """Add a project: bare-clone source if it's a URL, else track it as a local checkout.

    A bare clone keeps ``repo/`` free of a checked-out working tree — all work happens in
    goal worktrees, never in ``repo/`` itself, and a checked-out tree there only confuses
    git GUIs into treating it as a working checkout alongside its own worktrees. The fetch
    refspec, initial fetch and ``origin/HEAD`` symref are set up by hand since ``git clone
    --bare`` skips them, so ``default_branch``/``base_ref`` see the same ``origin/<default>``
    remote-tracking ref a normal clone would give them for free.

    ``checkout``, if given, also stands up a plain worktree of the default branch there in the
    same step — only valid when cloning a URL (a local-path source is already a checkout, so
    there's no "first checkout" gap to fill).
    """
    if _URL.search(source):
        name = re.split(r'[/:]', source.rstrip('/'))[-1].removesuffix('.git')
        project = workspace / name
        repo = project / 'repo'
        Git(workspace)('clone', '--bare', source, str(repo))
        git = Git(repo)
        git('config', 'remote.origin.fetch', '+refs/heads/*:refs/remotes/origin/*')
        git('fetch', '--prune', 'origin')
        git('remote', 'set-head', 'origin', '-a')
        if checkout is not None:
            worktree_add(
                repo, project / 'worktrees', branch=default_branch(git), path=checkout, fetch=False
            )
        return register(workspace, name, repo)
    if checkout is not None:
        raise UserError(
            f'--checkout only applies when cloning a git URL, not tracking a local path: {source}'
        )
    return track(workspace, Path(source))
