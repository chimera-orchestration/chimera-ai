from pathlib import Path

from giterator import Git, GitError

from chimera.commands.goal import ROLES


def new(repo: Path, worktrees_root: Path, goal: str, branch: str | None = None) -> list[Path]:
    """Create <goal>-human and <goal>-agent worktrees on branches <goal>/human and <goal>/agent.

    ``branch`` is the start point for the new branches. When omitted, it
    defaults to the most recently committed of local ``main`` and
    ``origin/main`` (not whatever the repo currently has checked out), falling
    back to ``HEAD`` if neither exists.
    """
    git = Git(repo)
    _require_commit(git, repo)
    base = branch or _base_ref(git) or 'HEAD'
    worktrees_root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for role in ROLES:
        worktree = worktrees_root / f'{goal}-{role}'
        git('worktree', 'add', '-b', f'{goal}/{role}', str(worktree), base)
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
