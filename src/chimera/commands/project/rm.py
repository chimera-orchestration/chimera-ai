import os
import shutil
from pathlib import Path

import yaml

from chimera.commands.worktree.rm import refuse_if_agents_running
from chimera.commands.worktree.rm import remove as remove_worktrees
from chimera.dry import Dry
from chimera.git import Git
from chimera.worktrees import goal_actors, goals, worktree_path


def remove(workspace: Path, name: str, force: bool = False, dry: Dry = Dry()) -> Path | None:
    """Remove a tracked project from the workspace; return the removed dir, or None.

    A no-op returning None if the project is already gone. Refuses while the
    project still has goals unless ``force``, which first finishes every goal —
    discarding unmerged/uncommitted work — before removing the project directory.
    A live agent in any worktree — or a project chat, which runs in the project dir
    itself — always aborts, even with ``force`` (unlike goal finish, whose --force
    bypasses the liveness check for a single goal); the project dir is swept even
    when there are no goals to check. Only the
    workspace's project directory is removed; a tracked repo living outside it is
    left untouched. A workspace-only repo (under the project dir, no remote) holding
    real work is the *sole* copy of that work — removal would be unrecoverable loss,
    the one failure the log can't undo — so it too refuses without ``force``. Under ``dry`` the same checks run but nothing is deleted — the
    goal teardown and the directory removal are both previewed — and the return is
    still the dir that *would* be removed.
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
    git = Git(repo)
    if not force and repo.resolve().is_relative_to(project.resolve()) and _sole_copy(git):
        raise RuntimeError(
            f'{name} holds the only copy of its work (no remote to recover from); '
            f'publish it first (ch project push) or use --force to discard it'
        )
    # check the project dir (where <project>@manager runs) and every goal's worktrees
    # before touching any of them
    refuse_if_agents_running(
        [project]
        + [
            wt
            for goal in sorted(existing)
            for actor in sorted(goal_actors(git, worktrees_root, goal))
            if (wt := worktree_path(worktrees_root, goal, actor)).is_dir()
        ]
    )
    for goal in sorted(existing):
        remove_worktrees(repo, worktrees_root, goal, force=True, dry=dry)
    dry(shutil.rmtree, project)
    return project


def _sole_copy(git: Git) -> bool:
    """Whether this repo holds the only copy of real work: no remote to recover from, and
    history beyond ``ch project new``'s single empty seed commit."""
    if git('remote').strip():
        return False
    match git('rev-list', '--all').split():
        case []:
            return False
        case [seed]:
            empty_tree = git('hash-object', '-t', 'tree', os.devnull).strip()
            return git('rev-parse', f'{seed}^{{tree}}').strip() != empty_tree
        case _:
            return True
