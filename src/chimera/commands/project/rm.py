import shutil
from pathlib import Path

import yaml

from chimera.commands.worktree.rm import remove as remove_worktrees
from chimera.worktrees import goals


def remove(workspace: Path, name: str, force: bool = False) -> Path | None:
    """Remove a tracked project from the workspace; return the removed dir, or None.

    A no-op returning None if the project is already gone. Refuses while the
    project still has goals unless ``force``, which first finishes every goal —
    discarding unmerged/uncommitted work — before removing the project directory.
    A live agent in any worktree always aborts, even with ``force``: the one
    safeguard goal finish never bypasses. Only the workspace's project directory
    is removed; a tracked repo living outside it is left untouched.
    """
    project = workspace / name
    if not project.exists():
        return None
    config = project / 'config.yaml'
    if not config.is_file():
        raise RuntimeError(f'{project} is not a tracked project (no config.yaml)')
    worktrees_root = project / 'worktrees'
    existing = goals(worktrees_root)
    if existing and not force:
        joined = ', '.join(sorted(existing))
        raise RuntimeError(
            f'{name} still has goals ({joined}); run `ch goal finish` on each or use --force'
        )
    repo = Path(yaml.safe_load(config.read_text())['repo'])
    for goal in sorted(existing):
        remove_worktrees(repo, worktrees_root, goal, force=True)
    shutil.rmtree(project)
    return project
