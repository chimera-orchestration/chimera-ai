import shutil
from pathlib import Path

import yaml

from chimera.commands.goal import ROLES
from chimera.commands.goal.cleanup import cleanup


def forget(workspace: Path, name: str, force: bool = False) -> Path | None:
    """Remove a tracked project from the workspace; return the removed dir, or None.

    A no-op returning None if the project is already gone. Refuses while the
    project still has goals unless ``force``, which first cleans up every goal —
    discarding unmerged/uncommitted work — before removing the project directory.
    A live agent in any worktree always aborts, even with ``force``: the one
    safeguard goal cleanup never bypasses. Only the workspace's project directory
    is removed; a tracked repo living outside it is left untouched.
    """
    project = workspace / name
    if not project.exists():
        return None
    config = project / 'config.yaml'
    if not config.is_file():
        raise RuntimeError(f'{project} is not a tracked project (no config.yaml)')
    worktrees_root = project / 'worktrees'
    goals = _goals(worktrees_root)
    if goals and not force:
        joined = ', '.join(sorted(goals))
        raise RuntimeError(
            f'{name} still has goals ({joined}); run `ch goal cleanup` on each or use --force'
        )
    repo = Path(yaml.safe_load(config.read_text())['repo'])
    for goal in sorted(goals):
        cleanup(repo, worktrees_root, goal, force=True)
    shutil.rmtree(project)
    return project


def _goals(worktrees_root: Path) -> set[str]:
    """Goal names discovered from the project's worktree directories."""
    if not worktrees_root.is_dir():
        return set()
    return {
        child.name.removesuffix(f'-{role}')
        for child in worktrees_root.iterdir()
        for role in ROLES
        if child.is_dir() and child.name.endswith(f'-{role}')
    }
