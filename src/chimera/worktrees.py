from pathlib import Path

from giterator import Git, GitError

AGENT = 'agent'
HUMAN = 'human'
ACTORS = (HUMAN, AGENT)
"""The default actor set: ``human`` works on a bare branch, ``agent`` in a worktree."""

SEP = '@'
"""Joins goal and actor in a flat worktree dir / session name.

The branch uses ``/`` (``<goal>/<actor>``), but a dir can't nest there and goals
are kebab-case, so a dash would blur the boundary (``my-goal-agent``). ``@`` is the
only safe seam across every filesystem, shell, tmux and git GUI — it can't appear in
a goal or actor slug, so ``rsplit(SEP, 1)`` always recovers the pair.
"""


def branch(goal: str, actor: str) -> str:
    """The branch name for an actor on a goal: ``<goal>/<actor>``."""
    return f'{goal}/{actor}'


def worktree_path(root: Path, goal: str, actor: str) -> Path:
    """The worktree directory for an actor on a goal: ``<root>/<goal>@<actor>``."""
    return root / f'{goal}{SEP}{actor}'


def session_name(project: str, goal: str, actor: str) -> str:
    """The agent session label: ``<project>@<goal>@<actor>``."""
    return SEP.join((project, goal, actor))


def worktree_dirs(root: Path) -> list[Path]:
    """Worktree directories present under root, sorted."""
    return sorted(child for child in root.iterdir() if child.is_dir()) if root.is_dir() else []


def goals(root: Path) -> set[str]:
    """Goal names present under root, derived from each goal's ``<goal>@agent`` worktree."""
    suffix = f'{SEP}{AGENT}'
    return {d.name.removesuffix(suffix) for d in worktree_dirs(root) if d.name.endswith(suffix)}


def registered_worktrees(git: Git) -> set[Path]:
    """The worktree paths git knows about for repo, resolved."""
    out = git('worktree', 'list', '--porcelain')
    return {
        Path(line.removeprefix('worktree ')).resolve()
        for line in out.splitlines()
        if line.startswith('worktree ')
    }


def is_merged(git: Git, ref: str) -> bool:
    """Whether ref is an ancestor of the repo's current HEAD (nothing unmerged)."""
    try:
        git('merge-base', '--is-ancestor', ref, 'HEAD')
        return True
    except GitError:
        return False


def is_dirty(worktree: Path) -> bool:
    """Whether the worktree has uncommitted or untracked changes."""
    return bool(Git(worktree)('status', '--porcelain').strip())


def _ref_exists(git: Git, ref: str) -> bool:
    try:
        git('rev-parse', '--verify', '--quiet', ref)
        return True
    except GitError:
        return False


def default_branch(git: Git) -> str:
    """The repo's default branch name (e.g. ``main`` or ``master``).

    Prefers the remote's published default (``origin/HEAD``, set by clone); else the first of
    ``main``/``master`` existing as a local or remote-tracking ref; else falls back to ``main``.
    """
    try:
        return (
            git('symbolic-ref', '--short', 'refs/remotes/origin/HEAD')
            .strip()
            .removeprefix('origin/')
        )
    except GitError:
        pass
    for name in ('main', 'master'):
        if _ref_exists(git, name) or _ref_exists(git, f'origin/{name}'):
            return name
    return 'main'


def base_ref(git: Git) -> str | None:
    """Start point for new branches: newest-committed of local ``<default>`` and ``origin/<default>``.

    ``<default>`` is the repo's :func:`default_branch`. Ties (e.g. both at the same commit)
    favour local. Returns ``None`` if neither ref exists.
    """
    default = default_branch(git)
    newest: str | None = None
    newest_committed = -1
    for ref in (default, f'origin/{default}'):
        try:
            committed = int(git('log', '-1', '--format=%ct', ref).strip())
        except GitError:
            continue
        if committed > newest_committed:
            newest, newest_committed = ref, committed
    return newest


def fetch_origin(git: Git) -> None:
    """Fetch ``origin`` with prune so remote-tracking refs are current. No-op without an origin."""
    if 'origin' in git('remote').split():
        git('fetch', '--prune', 'origin')
