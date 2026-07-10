from dataclasses import dataclass
from pathlib import Path

from giterator import GitError
from loguru import logger

from chimera.agents import Session
from chimera.commands.agent import stop
from chimera.commands.worktree.rm import remove
from chimera.config import UserError
from chimera.dry import Dry
from chimera.git import Git
from chimera.worktrees import (
    HUMAN,
    SEP,
    Checkout,
    branch,
    checkout_of,
    default_branch,
    fetch_origin_or_offline,
    goal_actors,
    goal_branch_actors,
    is_dirty,
    is_merged,
    registered_worktrees,
    worktree_path,
)


@dataclass(frozen=True)
class MergeResult:
    """What landing the goal did: which branch went where, and what was cleaned up after."""

    source: str  # the actor branch whose tip was landed
    into: str  # the base branch it landed on
    sha: str  # the landed tip (short) — base's own sha once the merge is real
    fastforwarded: bool  # False when base already contained the work
    landed: tuple[Checkout, ...] = ()  # plain checkouts moved off goal branches onto base
    stopped: tuple[Session, ...] = ()  # agent sessions stopped before the sweep
    removed: tuple[Path, ...] = ()  # worktrees swept


def merge(
    repo: Path,
    worktrees_root: Path,
    goal: str,
    into: str | None = None,
    force: bool = False,
    fetch: bool = True,
    dry: Dry = Dry(),
) -> MergeResult:
    """Land a finished goal on ``into`` (default: the repo's default branch), then clean up.

    The manager's finish-up in one command: pick the goal's **source** branch — the actor
    branch containing every other actor's work (see :func:`_source`; actors that have
    diverged refuse, pointing at ``goal sync``) — fast-forward ``into`` to its tip
    (:func:`_land`; a base with commits of its own refuses: rebase the goal first, in its
    worktree), move any plain checkout off the goal's branches onto the landed base so the
    sweep can delete them, stop live agent sessions in the goal's worktrees
    (:func:`chimera.commands.agent.stop`), and sweep the goal's branches and worktrees
    (``worktree rm``). The sweep runs forced — every check it would make has already been
    made here, in the right order around the fast-forward (and under ``dry`` the base
    hasn't really moved, so the unforced check would wrongly refuse) — which is safe
    because everything the sweep discards is, by then, contained in ``into``.

    ``force`` handles diverged actors the blunt way: the newest-committed actor branch is
    landed and the rest are discarded with the sweep (their shas recoverable from the
    ``worktree rm: refs`` line); it also skips the dirty-worktree refusal. It never forces
    the fast-forward itself — discarding ``into``'s own commits is the one loss the log
    couldn't cheaply undo. Idempotent: a re-run after a half-done landing finds the work
    already contained and carries on with the cleanup. ``fetch`` (the default) refreshes
    ``origin`` first. Under ``dry``, every check and decision runs but nothing moves, stops
    or is removed.
    """
    git = Git(repo)
    if fetch:
        fetch_origin_or_offline(git)
    actors = sorted(goal_branch_actors(git, goal))
    if not actors:
        raise UserError(f'nothing to merge — no actor branches for goal {goal!r}')
    base = into if into is not None else default_branch(git)
    if base.startswith(f'{goal}/'):
        raise UserError(f"{base} is one of {goal}'s own branches — name a base like main")
    if not git.ref_exists(base):
        raise UserError(f'no branch {base} to merge into')
    source = _source(git, goal, actors, force)
    registered = registered_worktrees(git)
    worktrees = [
        path
        for actor in sorted(goal_actors(git, worktrees_root, goal))
        if (path := worktree_path(worktrees_root, goal, actor)).resolve() in registered
    ]
    if not force:
        _refuse_if_dirty(worktrees)
    releases = _release_plan(git, goal, actors, base)
    fastforwarded = _land(git, goal, source, base, dry)
    # source's tip is where base lands even when --dry left base unmoved; taken now, before
    # the sweep deletes the branch it names
    sha = git.rev_parse(source if fastforwarded else base)
    landed = tuple(_release(git, checkout, ref, base, dry) for checkout, ref in releases)
    stopped = tuple(session for path in worktrees for session in stop(path, dry))
    removed = tuple(remove(repo, worktrees_root, goal, force=True, fetch=False, dry=dry))
    return MergeResult(source, base, sha, fastforwarded, landed, stopped, removed)


def _source(git: Git, goal: str, actors: list[str], force: bool) -> str:
    """The actor branch to land: the one that contains every other actor's work.

    Containment is :func:`chimera.worktrees.is_merged`, so a human branch that squashed
    the agent's commits still counts as containing them — and landing the container is
    what lets the sweep prove the others merged afterwards. No branch containing all the
    others means the actors have truly diverged: refused, pointing at ``goal sync`` (or
    ``force``, which lands the newest-committed branch and lets the sweep discard the
    rest). Equivalent tips tie in favour of ``human`` — the curated history. The choice
    lands a ``goal merge: source`` log line.
    """
    branches = [branch(goal, actor) for actor in actors]
    committed = {ref: int(git('log', '-1', '--format=%ct', ref)) for ref in branches}
    if force:
        chosen = max(branches, key=lambda ref: committed[ref])
        logger.bind(source=chosen, forced=True).info('goal merge: source')
        return chosen
    candidates = [
        ref
        for ref in branches
        if all(other == ref or is_merged(git, other, ref) for other in branches)
    ]
    if not candidates:
        raise UserError(
            f'no actor branch contains all the others ({", ".join(branches)}) — '
            f'ch goal sync {goal} so one does, or --force to land the newest-committed '
            f'and discard the rest'
        )
    human = branch(goal, HUMAN)
    chosen = human if human in candidates else max(candidates, key=lambda ref: committed[ref])
    logger.bind(source=chosen, candidates=candidates).info('goal merge: source')
    return chosen


def _refuse_if_dirty(worktrees: list[Path]) -> None:
    """Uncommitted work anywhere in the goal refuses the whole landing, before anything moves."""
    problems = [
        f'{path} has uncommitted or untracked changes' for path in worktrees if is_dirty(path)
    ]
    if problems:
        joined = '\n  '.join(problems)
        raise UserError(f'refusing to merge (use --force to discard):\n  {joined}')


def _land(git: Git, goal: str, source: str, base: str, dry: Dry) -> bool:
    """Advance ``base`` to ``source``'s tip — fast-forward only, never a forced move.

    Work already contained is a no-op (the idempotent re-run path). A ``base`` with
    commits of its own refuses: integrating them is rebase work for the goal's worktree,
    never something to guess at here — and deliberately beyond ``--force``, since
    discarding ``base``'s commits is the one loss the sweep's log couldn't undo. A
    checked-out ``base`` moves its work tree along (as ``goal sync`` does); the ref move
    rides a ``goal merge: refs`` log line.
    """
    if is_merged(git, source, base):
        return False
    try:
        git('merge-base', '--is-ancestor', base, source)
    except GitError:
        raise UserError(
            f'{base} has commits {source} lacks — rebase {source} onto {base} in its '
            f'worktree (git rebase {base}), then re-run'
        ) from None
    with git.ref_log('goal merge: refs', base, goal=goal, source=source):
        checkout = checkout_of(git, base)
        if checkout is None:  # bare branch — repoint the ref directly
            dry(git, 'branch', '-f', base, source)
        elif is_dirty(checkout):
            raise UserError(
                f'{base} is checked out with uncommitted changes at {checkout} — '
                f'commit or stash there first'
            )
        else:  # move the ref and its work tree together
            dry(Git(checkout), 'merge', '--ff-only', source)
    return True


def _release_plan(git: Git, goal: str, actors: list[str], base: str) -> list[tuple[Path, str]]:
    """Plain checkouts sitting on the goal's branches, each to be landed on ``base``.

    Git won't delete a checked-out branch, so the sweep needs them moved first. A managed
    ``<goal>@<actor>`` worktree is skipped — the sweep removes it whole; a plain checkout
    (e.g. the one ``goal sync``/``review`` landed a human on) gets ``base`` checked out
    instead, which is also where that human wants to be once the goal has landed. All
    refusals happen here, before anything mutates: a dirty checkout can't be flipped, and
    a ``base`` already checked out elsewhere couldn't be checked out here too.
    """
    plan: list[tuple[Path, str]] = []
    base_home = checkout_of(git, base)
    for actor in actors:
        ref = branch(goal, actor)
        checkout = checkout_of(git, ref)
        if checkout is None or SEP in checkout.name:
            continue
        if is_dirty(checkout):
            raise UserError(
                f'{ref} is checked out with uncommitted changes at {checkout} — '
                f'commit or stash there first'
            )
        if base_home is not None:
            raise UserError(
                f'{ref} is checked out at {checkout}, but {base} is already checked out at '
                f'{base_home} — git checkout something else there, then re-run'
            )
        base_home = checkout  # this checkout takes base; a second goal branch can't also
        plan.append((checkout, ref))
    return plan


def _release(git: Git, checkout: Path, ref: str, base: str, dry: Dry) -> Checkout:
    """Move the plain checkout at ``checkout`` from ``ref`` onto ``base``.

    A checkout is recovery-logged by where HEAD pointed either side (see
    ``agent-docs/logging.md``), keyed by branch — the same shape ``checkout_here`` lands.
    """

    def move() -> None:
        wt = Git(checkout)
        before = {ref: wt.rev_parse('HEAD', short=False)}
        wt('checkout', base)
        logger.bind(
            worktree=str(checkout),
            git={'before': before, 'after': {base: wt.rev_parse('HEAD', short=False)}},
        ).info('goal merge: refs')

    dry(move)
    return Checkout(done=True, where=checkout, branch=base, was=ref)
