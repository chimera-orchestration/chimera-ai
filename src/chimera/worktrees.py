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
