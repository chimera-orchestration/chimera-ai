from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from giterator import GitError

from chimera.config import UserError
from chimera.git import Git, RefLog
from chimera.worktrees import (
    AGENT,
    HUMAN,
    SEP,
    Checkout,
    branch,
    checkout_here,
    checkout_of,
    goal_branch_actors,
    is_dirty,
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
    FORCED = 'forced'  # --force: mover had diverged; repointed onto the target, its own commits
    # discarded (recoverable via the shas on the `goal sync: refs` line)
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
    discarded: int = 0  # commits dropped off the mover's tip (Outcome.FORCED only)
    conflict: Path | None = None  # the mover's checkout a conflict was left in (Outcome.CONFLICT)
    checkout: Checkout | None = None  # where the mover was landed in place, if anywhere


def sync(
    repo: Path,
    goal: str,
    mover: str | None = None,
    target: str | None = None,
    into: Path | None = None,
    force: bool = False,
) -> SyncResult:
    """Bring the goal's ``mover`` actor branch up to its ``target`` branch's work.

    ``mover``/``target`` are actor names, not literal ``human``/``agent`` — any goal actor
    (``reviewer``, a second agent, …) works. Passing neither defaults to ``human`` catching up
    to ``agent``. Passing exactly one infers the other from the goal's existing actor branches
    (:func:`chimera.worktrees.goal_branch_actors`): the one branch that isn't the actor you gave,
    when there's exactly one — with more than one candidate (a goal with a third actor already in
    play) inference is refused, listing them, so you pick with the other flag instead of getting
    silently pointed at the wrong one.

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
    history clobbered by target's raw tip. A range that contains merge commits is refused
    outright: once the target has rebased onto or merged in other work,
    ``<watermark>..<target>`` sweeps in history that is not the target's own (and
    ``git cherry-pick`` cannot replay a merge) — ``--force`` is the way through, or integrate by
    hand. The append replays via ``git cherry-pick`` in the
    mover's checkout (so a conflict is left there to resolve and ``git cherry-pick --continue``); a
    transient marker lets a re-run tell a finished append from an aborted one.

    ``force`` resolves a divergence the blunt way: instead of appending, the mover is repointed
    onto the target's tip, its own commits discarded — count and shas ride the ``goal sync: refs``
    line, so the log is enough to restore them. For when there's no integration record to append
    from, or the target was rebased and a replay would only conflict. The watermark moves to the
    target's tip, so the next un-forced sync appends incrementally again. Inert unless the two
    have actually diverged — a mover that merely leads keeps its lead. It also cleans up a broken
    append on its way through: a conflicted append of our own (the marker attributes it) is
    aborted, and a stray sequence stranded in the mover's clean checkout is quit, before the
    repoint.

    Refuses (``UserError``) when the target is missing, ``mover``/``target`` are the same, the mover
    isn't checked out anywhere an append can run or is dirty, a divergence has no integration
    record (and no ``force``), the commits to append include merges, or a cherry-pick is already
    in progress in the mover's checkout — ours (resolve and re-run, or ``force`` to discard it)
    or anyone's. A replay that dies without leaving a conflict to resolve is backed out, never
    left half-applied. Ref moves ride a ``goal sync: refs`` log line (see
    ``agent-docs/logging.md``). ``into`` (the caller's cwd) opts in to landing ``mover`` *in
    place* (see :func:`chimera.worktrees.checkout_here`): once it's settled — and, when an append
    finds the mover checked out nowhere, *before* the replay, so the append runs right there
    instead of refusing. The result carries what happened via ``SyncResult.checkout``. A conflict
    is never landed anywhere new after the fact — though an append that pre-landed in ``into``
    leaves you there, mid-conflict, to resolve.
    """
    git = Git(repo)
    mover, target = _resolve_actors(git, goal, mover, target)
    if mover == target:
        raise UserError(f'nothing to sync — --move and --to are both {mover!r}')
    mover_branch, target_branch = branch(goal, mover), branch(goal, target)
    if not git.ref_exists(target_branch):
        raise UserError(f'no branch {target_branch} to sync from')
    watermark = _watermark_ref(goal, mover)
    with git.ref_log('goal sync: refs', mover_branch, watermark, goal=goal) as refs:
        outcome, ahead_by, appended, discarded, conflict, landed = _apply(
            git, goal, mover, mover_branch, target_branch, watermark, refs, force, into
        )
    if landed is None and into is not None and outcome is not Outcome.CONFLICT:
        landed = checkout_here(git, mover_branch, into, 'goal sync')
    return SyncResult(
        outcome,
        mover,
        target,
        git.rev_parse(mover_branch),
        ahead_by,
        appended,
        discarded,
        conflict,
        landed,
    )


def _apply(
    git: Git,
    goal: str,
    mover: str,
    mover_branch: str,
    target_branch: str,
    watermark: str,
    refs: RefLog,
    force: bool,
    into: Path | None,
) -> tuple[Outcome, int, int, int, Path | None, Checkout | None]:
    """Settle ``mover_branch`` against ``target_branch``:
    ``(outcome, ahead_by, appended, discarded, conflict, landed)``."""
    _reconcile(git, goal, mover, mover_branch, watermark, force)
    tip = git.rev_parse(target_branch, short=False)
    if not git.ref_exists(mover_branch):
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
        return Outcome.AHEAD, ahead, 0, 0, None, None
    if force:
        return _force(git, mover_branch, target_branch, watermark, refs)
    return _append(git, goal, mover, mover_branch, target_branch, watermark, into)


def _resolve_actors(git: Git, goal: str, mover: str | None, target: str | None) -> tuple[str, str]:
    """Fill in whichever of ``mover``/``target`` was omitted (see :func:`sync`)."""
    match mover, target:
        case None, None:
            return HUMAN, AGENT
        case str() as m, str() as t:
            return m, t
        case str() as m, None:
            return m, _infer_other(git, goal, m, '--to')
        case None, str() as t:
            return _infer_other(git, goal, t, '--move'), t


def _infer_other(git: Git, goal: str, given: str, missing_flag: str) -> str:
    """The one other actor branch on ``goal`` besides ``given`` — refused when that's not unique."""
    candidates = sorted(goal_branch_actors(git, goal) - {given})
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise UserError(
            f'no other actor branch exists for {given!r} to sync with — pass {missing_flag} '
            f'explicitly'
        )
    listed = ', '.join(repr(c) for c in candidates)
    raise UserError(
        f"goal {goal!r} has multiple actors besides {given!r} ({listed}) — {missing_flag} "
        f'must be given explicitly'
    )


def _record(
    git: Git, watermark: str, tip: str, outcome: Outcome
) -> tuple[Outcome, int, int, int, None, None]:
    """Record ``tip`` as the integration watermark and report a non-append outcome."""
    git('update-ref', watermark, tip)
    return outcome, 0, 0, 0, None, None


def _force(
    git: Git, mover_branch: str, target_branch: str, watermark: str, refs: RefLog
) -> tuple[Outcome, int, int, int, None, None]:
    """Repoint a diverged mover onto the target, discarding the mover's own commits.

    The count rides the ``goal sync: refs`` line beside the before/after shas that make the
    discard recoverable; the watermark moves to the target's tip so the next un-forced sync
    appends incrementally again. A stray cherry-pick sequence in the mover's clean checkout
    (a replay that died mid-flight) is quit first — the repoint supersedes whatever it was doing.
    """
    checkout = checkout_of(git, mover_branch)
    if checkout is not None and not is_dirty(checkout) and _cherry_pick_in_progress(checkout):
        Git(checkout)('cherry-pick', '--quit')
    discarded = int(git('rev-list', '--count', f'{target_branch}..{mover_branch}'))
    _repoint(git, mover_branch, target_branch)
    refs.bind(discarded=discarded)
    _record(git, watermark, git.rev_parse(target_branch, short=False), Outcome.FORCED)
    return Outcome.FORCED, 0, 0, discarded, None, None


def _append(
    git: Git,
    goal: str,
    mover: str,
    mover_branch: str,
    target_branch: str,
    watermark: str,
    into: Path | None,
) -> tuple[Outcome, int, int, int, Path | None, Checkout | None]:
    """Replay the target commits made since the watermark onto a diverged mover branch.

    The replay needs the mover checked out; a mover checked out nowhere is materialised in
    ``into`` first when that's safe (:func:`chimera.worktrees.checkout_here` — a clean plain
    checkout of this repo), so a human standing in one never has to check it out by hand.
    """
    seeded = git.ref_shas(watermark).get(watermark)
    point = seeded or _tree_match_point(git, mover_branch, target_branch)
    if point is None:
        raise UserError(
            f'{mover_branch} has diverged from {target_branch} with no integration record — '
            f'rebase or cherry-pick by hand, or --force to repoint {mover_branch} onto '
            f'{target_branch}, discarding its own commits'
        )
    tip = git.rev_parse(target_branch, short=False)
    if seeded is None and point == tip:  # fresh squash-of-everything: repoint, don't just track it
        _repoint(git, mover_branch, target_branch)
        return _record(git, watermark, tip, Outcome.REPOINTED)
    new = git('rev-list', '--reverse', f'{point}..{target_branch}').split()
    if not new:
        return _record(git, watermark, tip, Outcome.NOOP)
    merges = git('rev-list', '--merges', f'{point}..{target_branch}').split()
    if merges:
        raise UserError(
            f'the {len(new)} commit(s) to append include {len(merges)} merge(s) — '
            f'{target_branch} was rebased or merged other work in, so an append would '
            f'replay history that is not its own; sync by hand, or --force to repoint '
            f'{mover_branch} onto {target_branch}, discarding its own commits'
        )
    checkout, landed = checkout_of(git, mover_branch), None
    if checkout is None and into is not None:
        landed = checkout_here(git, mover_branch, into, 'goal sync')
        if landed is not None:
            if not landed.done:
                raise UserError(
                    f'{landed.where} has uncommitted changes — commit or stash them so '
                    f'{mover_branch} can be checked out here for the append, then re-run'
                )
            checkout = landed.where
    if checkout is None:
        raise UserError(
            f'check out {mover_branch} to append {len(new)} commit(s) '
            f'(git checkout {mover_branch} …), then re-run'
        )
    if _cherry_pick_in_progress(checkout):
        raise UserError(
            f'a cherry-pick is already in progress at {checkout} — finish or abort it '
            f'(git cherry-pick --abort), then re-run'
        )
    if is_dirty(checkout):
        raise UserError(
            f'{mover_branch} is checked out with uncommitted changes at {checkout} — '
            f'commit or stash there first'
        )
    before = git.rev_parse(mover_branch, short=False)
    try:
        _replay(checkout, point, target_branch)
    except GitError as error:
        if _conflicted(checkout):  # stopped on a pick for the human: resolve/skip and --continue
            _write_marker(_marker(git, goal, mover), before, tip)
            return Outcome.CONFLICT, 0, 0, 0, checkout, landed
        # died some other way mid-replay — back it out rather than strand the mover half-appended
        # (any sequence state is this run's own: the guard above proved none existed before)
        if _cherry_pick_in_progress(checkout):
            Git(checkout)('cherry-pick', '--abort')
        raise UserError(f'append onto {mover_branch} failed and was rolled back:\n{error}')
    git('update-ref', watermark, tip)
    return Outcome.APPENDED, 0, len(new), 0, None, landed


def _replay(checkout: Path, point: str, target_branch: str) -> None:
    """Cherry-pick the append range in the mover's checkout.

    A seam: with merge-carrying ranges refused up front, no honest mid-replay failure is known,
    so tests inject one here to keep the rollback path proven.
    """
    Git(checkout)('cherry-pick', f'{point}..{target_branch}')


def _reconcile(
    git: Git, goal: str, mover: str, mover_branch: str, watermark: str, force: bool
) -> None:
    """Settle a prior append that hit a conflict, before deciding what this run should do.

    An in-progress cherry-pick blocks — unless ``force``, which backs it out (the marker
    attributes it to our own append, and the repoint force is headed for supersedes it anyway).
    Otherwise the marker is cleared and — if the mover moved since the append started — the
    watermark advances to what that append integrated (a finished resolve). A mover still at its
    pre-append sha means the user aborted, so nothing is recorded.
    """
    marker = _marker(git, goal, mover)
    state = _read_marker(marker)
    if state is None:
        return
    before, tip = state
    checkout = checkout_of(git, mover_branch)
    if checkout is not None and _cherry_pick_in_progress(checkout):
        if not force:
            raise UserError(
                f'an append is in progress at {checkout} — resolve and '
                f'`git cherry-pick --continue`, then re-run'
            )
        Git(checkout)('cherry-pick', '--abort')
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
    """Move ``mover_branch`` onto ``target_branch``, dropping mover's own commits off its tip.

    Unlike :func:`_fast_forward`, ``mover`` need not be ``target``'s ancestor — only callers that
    have already settled the loss may use this: the zero-diff repoint has proven the trees match
    (nothing is lost content-wise), and ``--force`` puts the discard on the log (and the dropped
    commits stay reachable via reflog).
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


def _conflicted(checkout: Path) -> bool:
    """A cherry-pick has stopped on a pick (conflict or empty), awaiting --continue/--skip."""
    return Git(checkout).ref_exists('CHERRY_PICK_HEAD')


def _cherry_pick_in_progress(checkout: Path) -> bool:
    """Any cherry-pick mid-flight: a stopped pick, or a live multi-commit sequence.

    ``CHERRY_PICK_HEAD`` alone misses a sequence that died *between* picks (nothing conflicted,
    but the sequencer state remains and blocks the next replay) — check both.
    """
    git = Git(checkout)
    return (
        git.ref_exists('CHERRY_PICK_HEAD')
        or Path(
            git('rev-parse', '--path-format=absolute', '--git-path', 'sequencer').strip()
        ).exists()
    )


def _is_ancestor(git: Git, ancestor: str, descendant: str) -> bool:
    try:
        git('merge-base', '--is-ancestor', ancestor, descendant)
        return True
    except GitError:
        return False
