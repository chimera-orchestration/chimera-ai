from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from chimera.agent_env import ai_session
from chimera.agents import Session
from chimera.commands.agent import live, stop
from chimera.config import UserError
from chimera.dry import Dry
from chimera.git import Git
from chimera.worktrees import (
    SEP,
    base_ref,
    branch,
    fetch_origin_or_offline,
    goal_actors,
    is_dirty,
    is_merged,
    registered_worktrees,
    worktree_path,
)


@dataclass(frozen=True)
class RemoveResult:
    """What the sweep did: sessions stopped first (``force`` only), then worktrees removed."""

    removed: tuple[Path, ...] = ()  # worktrees swept
    stopped: tuple[Session, ...] = ()  # live sessions stopped before the sweep (force only)


def remove(
    repo: Path,
    worktrees_root: Path,
    goal: str,
    force: bool = False,
    fetch: bool = True,
    dry: Dry = Dry(),
) -> RemoveResult:
    """Remove the goal's worktrees and branches; refuse on live agents or unsaved work
    unless force.

    Every actor in the goal's namespace is swept, not just the default human/agent pair
    (see :func:`goal_actors`) — any stray ``<goal>/<actor>`` branch or ``<goal>@<actor>``
    worktree goes too. Only touches worktrees/branches that actually exist, so re-running —
    or removing a goal that was never fully created — is a safe no-op. Every problem is
    gathered before refusing — agents live in the goal's worktrees (any registered
    harness), dirty worktrees, unmerged branches — so one refusal names them all (see
    :func:`_refuse_if_unsafe`). ``force`` stops any live session first
    (:func:`chimera.commands.agent.stop` — SIGTERM and wait, never SIGKILL; a session
    that can't be stopped still refuses, before anything is touched), then discards the
    unsaved work. ``fetch`` (the default) refreshes ``origin`` first so a branch merged
    upstream is recognised as merged. The deleted branches and the commits they pointed
    at are logged first (see ``agent-docs/logging.md``), so a force-discarded branch can
    still be recovered from the log. Under ``dry`` the same discovery and safety checks
    run but nothing is stopped or deleted (so no refs change and no ref line is logged);
    the return is still what *would* go.
    """
    git = Git(repo)
    registered = registered_worktrees(git)
    branches = set(git.branches())
    worktrees = {
        actor: worktree_path(worktrees_root, goal, actor)
        for actor in sorted(goal_actors(git, worktrees_root, goal))
    }
    present = [wt for wt in worktrees.values() if wt.resolve() in registered]
    stopped: tuple[Session, ...] = ()
    if force:
        stopped = tuple(session for worktree in present for session in stop(worktree, dry))
    else:
        if fetch:
            fetch_origin_or_offline(git)
        _refuse_if_unsafe(git, goal, worktrees, registered, branches, present)
    sync_refs = _sync_refs(git, goal)  # refs/chimera/synced/<goal>/* `goal sync` watermarks
    refs = tuple(branch(goal, actor) for actor in worktrees)
    removed: list[Path] = []
    with git.ref_log('worktree rm: refs', *refs, *sync_refs, goal=goal, force=force):
        for actor, worktree in worktrees.items():
            if worktree.resolve() in registered:
                dry(git, 'worktree', 'remove', *(('--force',) if force else ()), str(worktree))
                removed.append(worktree)
            if (ref := branch(goal, actor)) in branches:
                # -D not -d: _refuse_if_unsafe is the authority on what's safe to drop (it sees
                # squash/rebase merges that git's ancestry-only -d would wrongly call unmerged).
                dry(git, 'branch', '-D', ref)
        for ref in sync_refs:
            dry(git, 'update-ref', '-d', ref)
        _clear_markers(git, goal, dry)
    return RemoveResult(tuple(removed), stopped)


def _sync_refs(git: Git, goal: str) -> tuple[str, ...]:
    """The goal's ``goal sync`` watermark refs (``refs/chimera/synced/<goal>/*``)."""
    return tuple(git('for-each-ref', '--format=%(refname)', f'refs/chimera/synced/{goal}/').split())


def _clear_markers(git: Git, goal: str, dry: Dry) -> None:
    """Remove transient state the goal left in the shared git dir: ``goal sync``'s
    append-in-progress markers and ``goal pr``'s cached description."""
    chimera = (
        Path(git('rev-parse', '--path-format=absolute', '--git-common-dir').strip()) / 'chimera'
    )
    appending = chimera / 'appending'
    if appending.is_dir():
        for marker in appending.glob(f'{goal}{SEP}*'):
            dry(marker.unlink)
    description = chimera / 'pr' / goal
    if description.is_file():
        dry(description.unlink)


def live_problems(worktrees: Iterable[Path]) -> list[str]:
    """One line per live session: the worktree it occupies and how to recognise it."""
    return [
        f'an agent is live in {worktree}: {_describe(session)}'
        for worktree in worktrees
        for session in live(worktree)
    ]


def refuse_if_agents_running(worktrees: Iterable[Path]) -> None:
    if problems := live_problems(worktrees):
        raise UserError('\n'.join(problems) + '\nfind its terminal or kill the pid, then re-run')


def _describe(session: Session) -> str:
    fields = [f'pid {session.pid if session.pid is not None else "?"}']
    if session.kind:
        fields.append(session.kind)
    if session.status != '?':  # '?' is Session's absent-status fallback, not information
        fields.append(session.status)
    if session.started is not None:
        fields.append(f'since {session.started:%a %H:%M}')
    if session.name != session.id:  # the name falls back to the id; echoing it adds nothing
        fields.append(session.name)
    return '  '.join(fields)


def _refuse_if_unsafe(
    git: Git,
    goal: str,
    worktrees: dict[str, Path],
    registered: set[Path],
    branches: set[str],
    present: list[Path],
) -> None:
    """Gather *every* problem — live agents, dirty worktrees, unmerged branches — then
    refuse once, naming them all, so the user never fixes one refusal only to hit the
    next. The hint names only the fixes ``--force`` would actually apply, and is dropped
    entirely for an AI session — the flag is stripped from its tree, and capability a
    session can't reach is never signposted."""
    agents = live_problems(present)
    work: list[str] = []
    base = base_ref(git)
    for actor, worktree in worktrees.items():
        if worktree.resolve() in registered and is_dirty(worktree):
            work.append(f'{worktree} has uncommitted or untracked changes')
        ref = branch(goal, actor)
        if ref in branches and not (base is not None and is_merged(git, ref, base)):
            work.append(f'branch {ref} has unmerged commits')
    if not (agents or work):
        return
    joined = '\n  '.join(agents + work)
    fixes = [fix for fix, hit in (('stop the agents', agents), ('discard the work', work)) if hit]
    hint = '' if ai_session() else f'\nuse --force to {" and ".join(fixes)}'
    raise UserError(f'refusing to clean up:\n  {joined}{hint}')
