from pathlib import Path

from chimera.commands.worktree.add import add as worktree_add
from chimera.git import Git
from chimera.worktrees import default_branch


def checkout(
    repo: Path, worktrees: Path, path: Path, branch: str | None = None, fetch: bool = True
) -> str:
    """
    Check ``branch`` out as a plain worktree at ``path``; return the branch checked out.

    The discoverable name for ``worktree add``'s ad-hoc mode: ``branch`` defaults to the
    project's default branch, so a bare ``ch project checkout <path>`` stands up the
    checkout that ``project new``/``project add`` offer via ``--checkout``, for when that
    moment has passed. Everything else — existing vs new branch, upstream wiring, refusing
    a ``path`` under ``worktrees`` — is ``worktree add``'s behaviour, unchanged.
    """
    if branch is None:
        branch = default_branch(Git(repo))
    worktree_add(repo, worktrees, branch=branch, path=path, fetch=fetch)
    return branch
