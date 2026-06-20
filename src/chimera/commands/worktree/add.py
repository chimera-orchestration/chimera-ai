from pathlib import Path

from giterator import Git, GitError

from chimera.config import UserError
from chimera.worktrees import ACTORS, HUMAN, base_ref, branch, fetch_origin, worktree_path


def add(
    repo: Path,
    worktrees_root: Path,
    goal: str,
    actors: tuple[str, ...] = ACTORS,
    frm: str | None = None,
    fetch: bool = True,
) -> list[Path]:
    """Create branch ``<goal>/<actor>`` per actor and a worktree for each non-human actor.

    The human actor gets a bare branch, checked out on demand; every other actor
    gets a worktree at ``<goal>@<actor>`` on its branch. Branches and worktrees are
    created with no upstream tracking. Returns the created worktree paths.

    ``frm`` is the start point for the new branches. When omitted, it defaults to the
    most recently committed of the repo's default branch and its ``origin/`` tracking ref
    (not whatever the repo currently has checked out). When neither exists it refuses
    rather than silently grabbing the checked-out ``HEAD`` (the worst thing to inherit
    right after an adopt parks the repo on another branch) — pass ``frm`` to be explicit.
    ``fetch`` (the default) refreshes ``origin`` first so that base is current.
    """
    git = Git(repo)
    _require_commit(git, repo)
    if fetch:
        fetch_origin(git)
    base = frm or base_ref(git)
    if base is None:
        raise UserError(
            f'{repo}: no default branch (main/master) to branch from, '
            f'local or on origin — pass --from <ref>'
        )
    worktrees_root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for actor in actors:
        if actor == HUMAN:
            git('branch', '--no-track', branch(goal, actor), base)
        else:
            worktree = worktree_path(worktrees_root, goal, actor)
            git('worktree', 'add', '--no-track', '-b', branch(goal, actor), str(worktree), base)
            created.append(worktree)
    return created


def _require_commit(git: Git, repo: Path) -> None:
    try:
        git('rev-parse', '--verify', '--quiet', 'HEAD')
    except GitError:
        try:
            detail = f'\n\n{git("status")}'
        except GitError:
            detail = ''  # bare repo — no work tree to report on
        raise RuntimeError(
            f'{repo} has no commits to branch from — commit first:{detail}'
        ) from None
