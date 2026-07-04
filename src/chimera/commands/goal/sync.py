from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from giterator import Git, GitError
from loguru import logger

from chimera.config import UserError
from chimera.worktrees import (
    AGENT,
    HUMAN,
    SEP,
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
    NOOP = 'noop'  # already integrated up to the target
    FASTFORWARDED = 'fastforwarded'  # mover was behind; moved up to the target
    AHEAD = 'ahead'  # mover leads the target; nothing to do
    APPENDED = 'appended'  # mover diverged (squash); the new target commits were replayed onto it
    REPOINTED = 'repointed'  # mover's tip tree was a full, un-watermarked squash of target's own
    # current tip (zero diff) — moved the ref onto it rather than track a no-op watermark forever
    CONFLICT = 'conflict'  # the replay hit a conflict, left in the mover's checkout to resolve


@dataclass(frozen=True)
class SyncResult:
    """The outcome of a sync: which branch moved onto which, and where it ended up."""

    outcome: Outcome
    mover: str
    target: str
    sha: str  # the mover branch's sha after the run (short)
    ahead_by: int = 0  # commits the mover leads the target by (Outcome.AHEAD only)
    appended: int = 0  # commits replayed onto the mover (Outcome.APPENDED only)
    conflict: Path | None = None  # the mover's checkout a conflict was left in (Outcome.CONFLICT)
    checkout: Checkout | None = None  # where the mover was landed in place, if anywhere


def sync(
    repo: Path, goal: str, mover: str = HUMAN, target: str = AGENT, into: Path | None = None
) -> SyncResult:
    """Bring the goal's ``mover`` actor branch up to its ``target`` branch's work.

    Fast-forwards when it can; when ``mover`` has *diverged* — the common case after you squash the
    agent's commits on the human branch — it appends only the target commits made *since the last
    sync* (`<watermark>..<target>`) onto ``mover``, so your curated history gains the new work
    without re-applying what you already squashed. Materialises ``mover`` at the target when it
    doesn't exist, a no-op when already there, and leaves it when it leads the target.

    A **watermark** ref (``refs/chimera/synced/<goal>/<mover>``) records the target sha last
    integrated; it's set on every integrating outcome and is what makes the append know what's new.
    A legacy branch with no watermark is auto-seeded by matching the mover's tip *tree* to a target
    commit (a faithful squash preserves the tree); a squash that also carries your own edits can't
    be matched, so it's refused rather than guessed at. When that fresh seed lands exactly on
    target's own current tip — mover's tip is a full, zero-diff squash of everything target
    currently holds, so there's nothing to replay — the mover's ref is moved onto target directly
    (``Outcome.REPOINTED``) instead of recording a watermark that would never move it. This never
    happens once a watermark already exists for this mover: an *already-tracked* mover finding
    nothing new to append is the ordinary idempotent case, and must never have its own curated
    history clobbered by target's raw tip. The append replays via ``git cherry-pick`` in the
    mover's checkout (so a conflict is left there to resolve and ``git cherry-pick --continue``); a
    transient marker lets a re-run tell a finished append from an aborted one.

    Refuses (``UserError``) when the target is missing, ``mover``/``target`` are the same, the mover
    isn't checked out (an append needs a work tree) or is dirty, a divergence has no integration
    record, or an append is still in progress. Ref moves ride a ``goal sync: refs`` log line (see
    ``agent-docs/logging.md``). ``into`` (the caller's cwd) opts in to landing ``mover`` *in place*
    once it's settled (see :func:`chimera.worktrees.checkout_here`); the result carries what
    happened via ``SyncResult.checkout``. A conflict is never also landed elsewhere.
    """
    if mover == target:
        raise UserError(f'nothing to sync — --move and --to are both {mover!r}')
    git = Git(repo)
    mover_branch, target_branch = branch(goal, mover), branch(goal, target)
    if not _exists(git, target_branch):
        raise UserError(f'no branch {target_branch} to sync from')
    watermark = _watermark_ref(goal, mover)
    before = ref_shas(git, mover_branch, watermark)
    outcome, ahead_by, appended, conflict = _apply(
        git, goal, mover, mover_branch, target_branch, watermark
    )
    if (after := ref_shas(git, mover_branch, watermark)) != before:
        logger.bind(goal=goal, git={'before': before, 'after': after}).info('goal sync: refs')
    landed = (
        checkout_here(git, mover_branch, into, 'goal sync')
        if into is not None and outcome is not Outcome.CONFLICT
        else None
    )
    return SyncResult(
        outcome, mover, target, git.rev_parse(mover_branch), ahead_by, appended, conflict, landed
    )


def _apply(
    git: Git, goal: str, mover: str, mover_branch: str, target_branch: str, watermark: str
) -> tuple[Outcome, int, int, Path | None]:
    """Settle ``mover_branch`` against ``target_branch``: ``(outcome, ahead_by, appended, conflict)``."""
    _reconcile(git, goal, mover, mover_branch, watermark)
    tip = git.rev_parse(target_branch, short=False)
    if not _exists(git, mover_branch):
        git('branch', '--no-track', mover_branch, target_branch)
        return _record(git, watermark, tip, Outcome.CREATED)
    if git.rev_parse(mover_branch) == git.rev_parse(target_branch):
        return _record(git, watermark, tip, Outcome.NOOP)
    if _is_ancestor(git, mover_branch, target_branch):  # mover strictly behind — fast-forward
        _fast_forward(git, mover_branch, target_branch)
        return _record(git, watermark, tip, Outcome.FASTFORWARDED)
    if _is_ancestor(git, target_branch, mover_branch):  # mover leads — target fully contained
        ahead = int(git('rev-list', '--count', f'{target_branch}..{mover_branch}'))
        _record(git, watermark, tip, Outcome.AHEAD)
        return Outcome.AHEAD, ahead, 0, None
    return _append(git, goal, mover, mover_branch, target_branch, watermark)


def _record(git: Git, watermark: str, tip: str, outcome: Outcome) -> tuple[Outcome, int, int, None]:
    """Record ``tip`` as the integration watermark and report a non-append outcome."""
    git('update-ref', watermark, tip)
    return outcome, 0, 0, None


def _append(
    git: Git, goal: str, mover: str, mover_branch: str, target_branch: str, watermark: str
) -> tuple[Outcome, int, int, Path | None]:
    """Replay the target commits made since the watermark onto a diverged mover branch."""
    seeded = ref_shas(git, watermark).get(watermark)
    point = seeded or _tree_match_point(git, mover_branch, target_branch)
    if point is None:
        raise UserError(
            f'{mover_branch} has diverged from {target_branch} with no integration record — '
            f'rebase or cherry-pick by hand this time'
        )
    tip = git.rev_parse(target_branch, short=False)
    if seeded is None and point == tip:  # fresh squash-of-everything: repoint, don't just track it
        _repoint(git, mover_branch, target_branch)
        return _record(git, watermark, tip, Outcome.REPOINTED)
    new = git('rev-list', '--reverse', f'{point}..{target_branch}').split()
    if not new:
        return _record(git, watermark, tip, Outcome.NOOP)
    checkout = checkout_of(git, mover_branch)
    if checkout is None:
        raise UserError(
            f'check out {mover_branch} to append {len(new)} commit(s) '
            f'(git checkout {mover_branch} …), then re-run'
        )
    if is_dirty(checkout):
        raise UserError(
            f'{mover_branch} is checked out with uncommitted changes at {checkout} — '
            f'commit or stash there first'
        )
    before = git.rev_parse(mover_branch, short=False)
    try:
        Git(checkout)('cherry-pick', f'{point}..{target_branch}')
    except GitError:
        if not _cherry_pick_in_progress(checkout):
            raise  # pragma: no cover — a cherry-pick failure that isn't a merge conflict (the
            # validated non-empty range of fresh commits makes this unreachable in normal flow)
        _write_marker(_marker(git, goal, mover), before, tip)
        return Outcome.CONFLICT, 0, 0, checkout
    git('update-ref', watermark, tip)
    return Outcome.APPENDED, 0, len(new), None


def _reconcile(git: Git, goal: str, mover: str, mover_branch: str, watermark: str) -> None:
    """Settle a prior append that hit a conflict, before deciding what this run should do.

    An in-progress cherry-pick blocks; otherwise the marker is cleared and — if the mover moved
    since the append started — the watermark advances to what that append integrated (a finished
    resolve). A mover still at its pre-append sha means the user aborted, so nothing is recorded.
    """
    marker = _marker(git, goal, mover)
    state = _read_marker(marker)
    if state is None:
        return
    before, tip = state
    checkout = checkout_of(git, mover_branch)
    if checkout is not None and _cherry_pick_in_progress(checkout):
        raise UserError(
            f'an append is in progress at {checkout} — resolve and `git cherry-pick --continue`, '
            f'then re-run'
        )
    marker.unlink()
    if git.rev_parse(mover_branch, short=False) != before:  # finished — the mover advanced
        git('update-ref', watermark, tip)


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


def _repoint(git: Git, mover_branch: str, target_branch: str) -> None:
    """Move ``mover_branch`` onto ``target_branch`` when their tips are a proven zero-diff match.

    Unlike :func:`_fast_forward`, ``mover`` need not be ``target``'s ancestor — safe only because
    the caller has already matched the trees exactly, so nothing is lost content-wise even though
    mover's own commit(s) drop off the branch tip (still reachable via reflog).
    """
    checkout = checkout_of(git, mover_branch)
    if checkout is None:  # bare branch — repoint the ref directly
        git('branch', '-f', mover_branch, target_branch)
    elif is_dirty(checkout):
        raise UserError(
            f'{mover_branch} is checked out with uncommitted changes at {checkout} — '
            f'commit or stash there first'
        )
    else:  # move the ref and its work tree together
        Git(checkout)('reset', '--hard', target_branch)


def _tree_match_point(git: Git, mover_branch: str, target_branch: str) -> str | None:
    """The newest target commit whose tree matches the mover's tip — a faithful squash's origin.

    A squash preserves the final tree, so ``mover.tip.tree`` equals the tree of the target commit it
    was squashed from; that commit is the integration point. ``None`` when no target commit's tree
    matches (the mover carries edits of its own, so what's already integrated can't be inferred).
    """
    base = git('merge-base', mover_branch, target_branch).strip()
    mover_tree = git('rev-parse', f'{mover_branch}^{{tree}}').strip()
    for commit in git('rev-list', f'{base}..{target_branch}').split():  # newest first
        if git('rev-parse', f'{commit}^{{tree}}').strip() == mover_tree:
            return commit
    return None


def _watermark_ref(goal: str, mover: str) -> str:
    return f'refs/chimera/synced/{goal}/{mover}'


def _marker(git: Git, goal: str, mover: str) -> Path:
    """The transient append-in-progress marker for (goal, mover), in the shared git dir."""
    common = Path(git('rev-parse', '--path-format=absolute', '--git-common-dir').strip())
    return common / 'chimera' / 'appending' / f'{goal}{SEP}{mover}'


def _write_marker(marker: Path, before: str, tip: str) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f'before={before}\ntarget={tip}\n')


def _read_marker(marker: Path) -> tuple[str, str] | None:
    """``(before, target)`` from the marker, or ``None`` when there's no append in flight."""
    if not marker.exists():
        return None
    fields = dict(line.split('=', 1) for line in marker.read_text().splitlines())
    return fields['before'], fields['target']


def _cherry_pick_in_progress(checkout: Path) -> bool:
    return _exists(Git(checkout), 'CHERRY_PICK_HEAD')


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
