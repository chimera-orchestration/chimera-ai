from pathlib import Path

from giterator import GitError
from loguru import logger

from chimera.commands.project.checkout import checkout as project_checkout
from chimera.config import UserError
from chimera.dry import Dry
from chimera.git import Git
from chimera.worktrees import default_branch


def push(
    repo: Path,
    url: str,
    dry: Dry = Dry(),
    checkout: Path | None = None,
    worktrees: Path | None = None,
) -> str:
    """Push the default branch to ``url`` and wire it in as ``origin``; return the branch.

    Graduates a workspace-only project (``ch project new``) into an ordinary remote-backed
    one, after which nothing distinguishes the two. Only the default branch is published —
    ``{goal}/{actor}`` branches are local scratch by design (created ``--no-track``). The
    push goes straight to the URL *before* any config is written, so a failed push leaves
    zero config behind; only then is ``origin`` added, fetched (so ``origin/<default>``
    exists, as ``ch project add`` arranges for a clone), its ``HEAD`` set, and the default
    branch's upstream wired — the default branch only. ``origin/HEAD`` is set to the pushed
    branch explicitly, never ``set-head -a``: the freshly-graduated remote's unborn ``HEAD``
    may still name a different branch (``init.defaultBranch``), which ``-a`` can't resolve —
    and the branch just pushed *is* the local default, so it needs no asking. Refuses when
    an origin already
    exists (change it with ``git remote`` directly) or there is no default branch to push.

    ``checkout``, if given, also stands up a plain worktree of the pushed branch there once
    the wiring is done — the same follow-on ``project new``/``project add`` offer, so
    graduating and getting a checkout is one command. It needs ``worktrees`` (the project's
    worktrees root, which the checkout must sit outside) alongside it.
    """
    if checkout is not None and worktrees is None:
        raise TypeError('checkout requires worktrees')
    git = Git(repo)
    if 'origin' in git('remote').split():
        existing = git('remote', 'get-url', 'origin').strip()
        raise UserError(f'{repo} already has an origin ({existing}) — change it with git remote')
    branch = default_branch(git)
    if not git.ref_exists(branch):
        raise UserError(f'{repo} has no {branch} branch to push — commit something first')
    try:
        dry(git, 'push', url, branch)
    except GitError as error:
        raise UserError(f'push to {url} failed — no origin recorded:\n{error}') from None
    dry(git, 'remote', 'add', 'origin', url)
    dry(git, 'fetch', '--prune', 'origin')
    dry(git, 'remote', 'set-head', 'origin', branch)
    dry(git, 'branch', f'--set-upstream-to=origin/{branch}', branch)
    dry(
        logger.bind(url=url, branch=branch, sha=git.rev_parse(branch, short=False)).info,
        'project push: pushed',
    )
    if checkout is not None and worktrees is not None:
        dry(project_checkout, repo, worktrees, checkout, branch, fetch=False)
    return branch
