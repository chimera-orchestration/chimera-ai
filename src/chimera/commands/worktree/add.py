from pathlib import Path

from giterator import Git, GitError

from chimera.worktrees import ACTORS, HUMAN, branch, worktree_path


def add(
    repo: Path,
    worktrees_root: Path,
    goal: str,
    actors: tuple[str, ...] = ACTORS,
    frm: str | None = None,
) -> list[Path]:
    """Create branch ``<goal>/<actor>`` per actor and a worktree for each non-human actor.

    The human actor gets a bare branch, checked out on demand; every other actor
    gets a worktree at ``<goal>@<actor>`` on its branch. Branches and worktrees are
    created with no upstream tracking. Returns the created worktree paths.

    ``frm`` is the start point for the new branches. When omitted, it defaults to
    the most recently committed of local ``main`` and ``origin/main`` (not whatever
    the repo currently has checked out), falling back to ``HEAD`` if neither exists.
    """
    git = Git(repo)
    _require_commit(git, repo)
    base = frm or _base_ref(git) or 'HEAD'
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


def _base_ref(git: Git) -> str | None:
    """Return the most recently committed of local ``main`` and ``origin/main``.

    Ties (e.g. both pointing at the same commit) favour local ``main``.
    Returns ``None`` if neither ref exists.
    """
    newest: str | None = None
    newest_committed = -1
    for ref in ('main', 'origin/main'):
        try:
            committed = int(git('log', '-1', '--format=%ct', ref).strip())
        except GitError:
            continue
        if committed > newest_committed:
            newest, newest_committed = ref, committed
    return newest


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
