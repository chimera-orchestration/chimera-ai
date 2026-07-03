from pathlib import Path

from giterator import Git, GitError

from chimera.config import UserError
from chimera.worktrees import (
    DEFAULT_ACTORS,
    HUMAN,
    base_ref,
    fetch_origin,
    ref_exists,
    worktree_path,
)
from chimera.worktrees import branch as goal_branch


def add(
    repo: Path,
    worktrees_root: Path,
    *,
    goal: str | None = None,
    actors: tuple[str, ...] | None = None,
    branch: str | None = None,
    path: Path | None = None,
    frm: str | None = None,
    fetch: bool = True,
) -> list[Path]:
    """Create a worktree: a goal's actor branches (``goal``), or one ad-hoc branch at an
    explicit path (``branch``+``path``). Exactly one of the two shapes must be given.

    Goal mode creates branch ``<goal>/<actor>`` per actor (default just ``agent``) and a
    worktree for each non-human actor under ``worktrees_root``, forcing ``--no-track`` —
    unchanged from before this became one of two modes.

    Ad-hoc mode checks ``branch`` out (existing or new) as a plain worktree at ``path``, which
    must sit outside ``worktrees_root`` — that tree is reserved for the ``<goal>@<actor>`` shape
    the rest of chimera (doctor's checks in particular) assumes.
    """
    if goal is not None:
        if branch is not None or path is not None:
            raise UserError('--goal is mutually exclusive with <branch>/<path>')
        return _add_goal(repo, worktrees_root, goal, actors or DEFAULT_ACTORS, frm, fetch)
    if actors is not None:
        raise UserError('--actor requires --goal')
    if branch is None or path is None:
        raise UserError('<branch> and <path> are required unless --goal is given')
    if path.resolve().is_relative_to(worktrees_root.resolve()):
        raise UserError(f'{path}: use --goal to create a worktree under {worktrees_root}')
    return [_checkout(repo, branch, path, frm, fetch)]


def _add_goal(
    repo: Path,
    worktrees_root: Path,
    goal: str,
    actors: tuple[str, ...],
    frm: str | None,
    fetch: bool,
) -> list[Path]:
    """Create branch ``<goal>/<actor>`` per actor and a worktree for each non-human actor.

    By default only the agent is created (``DEFAULT_ACTORS``); ``human`` and any ad-hoc actors are
    materialised on demand by ``goal sync``. When ``human`` is named explicitly it gets a bare
    branch, checked out on demand; every other actor gets a worktree at ``<goal>@<actor>`` on its
    branch. Branches and worktrees are created with no upstream tracking.
    """
    git = Git(repo)
    _require_commit(git, repo)
    if fetch:
        fetch_origin(git)
    base = _resolve_base(git, repo, frm)
    worktrees_root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for actor in actors:
        if actor == HUMAN:
            git('branch', '--no-track', goal_branch(goal, actor), base)
        else:
            worktree = worktree_path(worktrees_root, goal, actor)
            git(
                'worktree', 'add', '--no-track', '-b', goal_branch(goal, actor), str(worktree), base
            )
            created.append(worktree)
    return created


def _checkout(repo: Path, branch: str, path: Path, frm: str | None, fetch: bool) -> Path:
    """Check ``branch`` out as a plain worktree at ``path`` — as-is if it already exists (with an
    upstream-tracking fixup, see below), else newly created from ``frm``/:func:`base_ref`.

    A bare clone's mirrored branches carry none of the ``branch.<name>.remote``/``.merge``
    tracking config a normal clone sets up for free, so plain ``git push``/``pull`` would
    silently need ``-u`` forever; when ``origin/<branch>`` exists and no upstream is set yet,
    this wires it up once in ``repo`` — shared repo-wide config, so every future worktree of
    that branch inherits it too. A newly created branch gets no such forcing either way — it's
    not a goal actor branch, so git's normal auto-tracking behaviour applies.
    """
    git = Git(repo)
    _require_commit(git, repo)
    if fetch:
        fetch_origin(git)
    path.parent.mkdir(parents=True, exist_ok=True)
    if ref_exists(git, branch):
        git('worktree', 'add', str(path), branch)
        origin_ref = f'origin/{branch}'
        upstream = git('for-each-ref', '--format=%(upstream)', f'refs/heads/{branch}').strip()
        if not upstream and ref_exists(git, origin_ref):
            git('branch', f'--set-upstream-to={origin_ref}', branch)
    else:
        base = _resolve_base(git, repo, frm)
        git('worktree', 'add', '-b', branch, str(path), base)
    return path


def _resolve_base(git: Git, repo: Path, frm: str | None) -> str:
    base = frm or base_ref(git)
    if base is None:
        raise UserError(
            f'{repo}: no default branch (main/master) to branch from, '
            f'local or on origin — pass --from <ref>'
        )
    return base


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
