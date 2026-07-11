import os
from pathlib import Path

from chimera.commands.project.track import register
from chimera.commands.worktree.add import add as worktree_add
from chimera.config import UserError
from chimera.git import Git

BRANCH = 'main'
"""Forced initial branch: :func:`chimera.worktrees.default_branch` only knows main/master,
so a machine whose ``init.defaultBranch`` is anything else would create a repo chimera
couldn't find the default branch of."""

SEED_MESSAGE = 'Empty seed commit (ch project new)'


def new(workspace: Path, name: str, checkout: Path | None = None) -> Path:
    """Create a workspace-only project: a fresh bare repo at ``{name}/repo``, no remote.

    Everything downstream (layout, config.yaml, goal/worktree/agent lifecycle) is identical
    to a URL-added project — ``ch project push`` later adds an origin and nothing
    distinguishes the two. The repo is seeded with an empty-tree commit via plumbing (no
    README or other opinionated content) so the first ``goal start`` has a commit to branch
    from; the commit uses the user's own git identity, resolved as git normally would.
    ``checkout``, if given, also stands up a plain worktree of ``main`` there, exactly as
    ``ch project add --checkout`` does for a clone.
    """
    project = workspace / name
    repo = project / 'repo'
    if (project / 'config.yaml').exists() or repo.exists():
        raise UserError(f'project {name} already exists at {project}')
    git = Git(repo)
    git.init(branch=BRANCH, bare=True)
    empty_tree = git('hash-object', '-w', '-t', 'tree', os.devnull).strip()
    with git.ref_log('project new: refs', BRANCH):
        seed = git('commit-tree', empty_tree, '-m', SEED_MESSAGE).strip()
        git('update-ref', f'refs/heads/{BRANCH}', seed)
    if checkout is not None:
        worktree_add(repo, project / 'worktrees', branch=BRANCH, path=checkout, fetch=False)
    return register(workspace, name, repo)
