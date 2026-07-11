from collections.abc import Iterable
from pathlib import Path

from chimera.agents import Session
from chimera.commands.agent import live
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


def remove(
    repo: Path,
    worktrees_root: Path,
    goal: str,
    force: bool = False,
    fetch: bool = True,
    dry: Dry = Dry(),
) -> list[Path]:
    """Remove the goal's worktrees and branches; refuse on unsaved work unless force.

    Every actor in the goal's namespace is swept, not just the default human/agent pair
    (see :func:`goal_actors`) — any stray ``<goal>/<actor>`` branch or ``<goal>@<actor>``
    worktree goes too. Only touches worktrees/branches that actually exist, so re-running —
    or removing a goal that was never fully created — is a safe no-op. Refuses if an agent
    from any registered harness is live in any of the goal's worktrees, unless force.
    ``fetch`` (the default) refreshes ``origin`` first so a branch merged upstream is
    recognised as merged. The deleted branches and the commits they pointed at are logged
    first (see ``agent-docs/logging.md``), so a force-discarded branch can still be
    recovered from the log. Under ``dry`` the same discovery and safety checks run but
    nothing is deleted (so no refs change and no ref line is logged); the return is still
    what *would* be removed. Returns removed worktrees.
    """
    git = Git(repo)
    registered = registered_worktrees(git)
    branches = set(git.branches())
    worktrees = {
        actor: worktree_path(worktrees_root, goal, actor)
        for actor in sorted(goal_actors(git, worktrees_root, goal))
    }
    if not force:
        refuse_if_agents_running(wt for wt in worktrees.values() if wt.resolve() in registered)
        if fetch:
            fetch_origin_or_offline(git)
        _refuse_if_unsafe(git, goal, worktrees, registered, branches)
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
    return removed


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


def refuse_if_agents_running(worktrees: Iterable[Path]) -> None:
    blocks: list[str] = []
    for worktree in worktrees:
        if sessions := live(worktree):
            described = '\n  '.join(_describe(session) for session in sessions)
            blocks.append(f'an agent is live in {worktree}:\n  {described}')
    if blocks:
        raise RuntimeError('\n'.join(blocks) + '\nfind its terminal or kill the pid, then re-run')


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
    git: Git, goal: str, worktrees: dict[str, Path], registered: set[Path], branches: set[str]
) -> None:
    base = base_ref(git)
    problems: list[str] = []
    for actor, worktree in worktrees.items():
        if worktree.resolve() in registered and is_dirty(worktree):
            problems.append(f'{worktree} has uncommitted or untracked changes')
        ref = branch(goal, actor)
        if ref in branches and not (base is not None and is_merged(git, ref, base)):
            problems.append(f'branch {ref} has unmerged commits')
    if problems:
        joined = '\n  '.join(problems)
        raise RuntimeError(f'refusing to clean up (use --force to discard):\n  {joined}')
