from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from giterator import Git, GitError
from loguru import logger

from chimera.config import UserError
from chimera.worktrees import (
    AGENT,
    HUMAN,
    Checkout,
    branch,
    checkout_here,
    checkout_of,
    is_dirty,
    ref_shas,
)


class Outcome(Enum):
    """What ``sync`` did — every non-error path a run can settle on."""

    CREATED = 'created'  # mover branch didn't exist; materialised at the target's tip
    NOOP = 'noop'  # already at the target
    FASTFORWARDED = 'fastforwarded'  # mover was behind; moved up to the target
    AHEAD = 'ahead'  # mover leads the target; nothing to do


@dataclass(frozen=True)
class SyncResult:
    """The outcome of a sync: which branch moved onto which, and where it ended up."""

    outcome: Outcome
    mover: str
    target: str
    sha: str  # the mover branch's sha after the run (short)
    ahead_by: int = 0  # commits the mover leads the target by (Outcome.AHEAD only)
    checkout: Checkout | None = None  # where the mover was landed in place, if anywhere


def sync(
    repo: Path, goal: str, mover: str = HUMAN, target: str = AGENT, into: Path | None = None
) -> SyncResult:
    """Fast-forward the goal's ``mover`` actor branch up to its ``target`` branch's tip.

    Materialises ``mover`` at the target when it doesn't yet exist (so a spike's human branch is
    born only when wanted), fast-forwards it when it's strictly behind, and does nothing when it's
    already there or leads the target. A fast-forward moves the work tree too when ``mover`` is
    checked out (refusing on uncommitted changes); a bare branch is repointed directly.

    Idempotent: re-running settles on ``NOOP``/``AHEAD`` once there's nothing to move. Refuses
    (``UserError``) when the target is missing, ``mover`` and ``target`` name the same branch, the
    two have diverged (a human must rebase), or ``mover``'s checkout is dirty. The mover branch and
    the sha it pointed at are logged before/after any move (see ``agent-docs/logging.md``).

    ``into`` (the caller's cwd) opts in to landing ``mover`` *in place*: once the branch is settled,
    it is checked out in the checkout at ``into`` when that's a clean plain checkout of ``repo``
    (see :func:`chimera.worktrees.checkout_here`), so a human runs one command instead of a manual
    ``git checkout``. The result carries what happened via ``SyncResult.checkout``.
    """
    if mover == target:
        raise UserError(f'nothing to sync — --move and --to are both {mover!r}')
    git = Git(repo)
    mover_branch, target_branch = branch(goal, mover), branch(goal, target)
    if not _exists(git, target_branch):
        raise UserError(f'no branch {target_branch} to sync from')
    before = ref_shas(git, mover_branch)
    outcome, ahead_by = _apply(git, mover_branch, target_branch)
    if (after := ref_shas(git, mover_branch)) != before:
        logger.bind(goal=goal, git={'before': before, 'after': after}).info('goal sync: refs')
    checkout = checkout_here(git, mover_branch, into, 'goal sync') if into is not None else None
    return SyncResult(outcome, mover, target, git.rev_parse(mover_branch), ahead_by, checkout)


def _apply(git: Git, mover_branch: str, target_branch: str) -> tuple[Outcome, int]:
    """Move ``mover_branch`` up to ``target_branch`` as far as a fast-forward allows."""
    if not _exists(git, mover_branch):
        git('branch', '--no-track', mover_branch, target_branch)
        return Outcome.CREATED, 0
    if git.rev_parse(mover_branch) == git.rev_parse(target_branch):
        return Outcome.NOOP, 0
    if _is_ancestor(git, mover_branch, target_branch):  # mover strictly behind — fast-forward
        _fast_forward(git, mover_branch, target_branch)
        return Outcome.FASTFORWARDED, 0
    if _is_ancestor(git, target_branch, mover_branch):  # mover leads — leave it
        return Outcome.AHEAD, int(git('rev-list', '--count', f'{target_branch}..{mover_branch}'))
    raise UserError(f'{mover_branch} has diverged from {target_branch} — rebase it first')


def _fast_forward(git: Git, mover_branch: str, target_branch: str) -> None:
    """Advance ``mover_branch`` to ``target_branch``, carrying its work tree if checked out."""
    checkout = checkout_of(git, mover_branch)
    if checkout is None:  # bare branch — repoint the ref directly
        git('branch', '-f', mover_branch, target_branch)
    elif is_dirty(checkout):
        raise UserError(
            f'{mover_branch} is checked out with uncommitted changes at {checkout} — '
            f'commit or stash there first'
        )
    else:  # move the ref and its work tree together
        Git(checkout)('merge', '--ff-only', target_branch)


def _exists(git: Git, ref: str) -> bool:
    try:
        git('rev-parse', '--verify', '--quiet', ref)
        return True
    except GitError:
        return False


def _is_ancestor(git: Git, ancestor: str, descendant: str) -> bool:
    try:
        git('merge-base', '--is-ancestor', ancestor, descendant)
        return True
    except GitError:
        return False
