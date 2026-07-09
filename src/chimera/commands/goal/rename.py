from dataclasses import dataclass
from pathlib import Path

from giterator import GitError
from loguru import logger

from chimera.commands.worktree.rm import refuse_if_agents_running
from chimera.config import UserError
from chimera.git import Git
from chimera.worktrees import (
    SEP,
    branch,
    goal_actors,
    registered_worktrees,
    require_valid_goal,
    worktree_path,
)


@dataclass(frozen=True)
class RenameResult:
    """What ``goal rename`` moved, and what it left for a human."""

    branches: list[tuple[str, str]]  # (old ref, new ref) renamed
    worktrees: list[tuple[Path, Path]]  # (old path, new path) moved
    warnings: list[str]  # things left with the old name (remote branches, unregistered dirs)
    cwd_moved_to: Path | None  # where cwd now lives, when it was inside a moved worktree


def rename(
    repo: Path, worktrees_root: Path, old: str, new: str, cwd: Path | None = None
) -> RenameResult:
    """Rename goal ``old`` to ``new`` across everything local; never touch a remote.

    Every ``<old>/<actor>`` branch is renamed (``git branch -m``, so any checkout's HEAD
    follows), every ``<old>@<actor>`` worktree is moved (``git worktree move``), and the
    goal's ``goal sync`` state — watermark refs and append markers — is carried along.
    Refuses while an agent is live in any of the goal's worktrees, when anything already
    exists under ``new`` on an actor the goal has (a real collision), when a bare branch
    ``new`` blocks the ``new/*`` namespace, and on a name git (or the ``@`` separator)
    can't hold. Idempotent: each actor's branch/worktree moves only while still under the
    old name, so a rename that died half-way completes on re-run. A remote branch
    ``<remote>/<old>/<actor>`` is warned about but never changed — renaming it is the
    human's call. The renamed refs are logged before/after (see ``agent-docs/logging.md``).
    """
    git = Git(repo)
    actors = sorted(goal_actors(git, worktrees_root, old))
    if not actors:
        raise UserError(f'no goal {old!r} to rename')
    _validate_name(old, new)
    branches = set(git.branches())
    registered = registered_worktrees(git)
    _refuse_collisions(git, worktrees_root, old, new, actors, branches)
    refuse_if_agents_running(
        wt for a in actors if (wt := worktree_path(worktrees_root, old, a)).resolve() in registered
    )
    renames = [(branch(old, a), branch(new, a)) for a in actors if branch(old, a) in branches]
    marks = _watermark_renames(git, old, new)
    moved: list[tuple[Path, Path]] = []
    warnings: list[str] = []
    all_refs = [ref for pair in (*renames, *marks) for ref in pair]
    with git.ref_log('goal rename: refs', *all_refs, goal=old, renamed_to=new):
        for old_ref, new_ref in renames:
            git('branch', '-m', old_ref, new_ref)
        for actor in actors:
            old_wt = worktree_path(worktrees_root, old, actor)
            if old_wt.resolve() in registered:
                new_wt = worktree_path(worktrees_root, new, actor)
                git('worktree', 'move', str(old_wt), str(new_wt))
                moved.append((old_wt, new_wt))
            elif old_wt.is_dir():
                warnings.append(
                    f'{old_wt} is not a registered worktree — left in place (see ch doctor)'
                )
        for old_mark, new_mark in marks:
            git('update-ref', new_mark, git.rev_parse(old_mark, short=False))
            git('update-ref', '-d', old_mark)
        _rename_markers(git, old, new)
    if moved:
        logger.bind(moved={str(o): str(n) for o, n in moved}).info('goal rename: worktrees')
    warnings.extend(_remote_warnings(git, old, actors))
    warnings.extend(_upstream_warnings(git, renames))
    if warnings:  # what the rename found and left alone — on record, not just on the console
        logger.bind(warnings=warnings).warning('goal rename: warnings')
    return RenameResult(renames, moved, warnings, _cwd_moved_to(cwd, moved))


def _validate_name(old: str, new: str) -> None:
    if new == old:
        raise UserError(f'new name {new!r} is the same as the old')
    require_valid_goal(new)


def _refuse_collisions(
    git: Git, worktrees_root: Path, old: str, new: str, actors: list[str], branches: set[str]
) -> None:
    """Refuse when ``new`` is blocked — checked per actor, so a half-done rename (old side
    already gone) resumes instead of reading its own progress as a collision."""
    if git.ref_exists(f'refs/heads/{new}'):
        raise UserError(
            f'branch {new!r} exists — git cannot hold refs/heads/{new} '
            f'beside refs/heads/{new}/<actor> (ch goal adopt {new}?)'
        )
    for actor in actors:
        if branch(old, actor) in branches and branch(new, actor) in branches:
            raise UserError(f'branch {branch(new, actor)} already exists')
        old_wt = worktree_path(worktrees_root, old, actor)
        new_wt = worktree_path(worktrees_root, new, actor)
        if old_wt.exists() and new_wt.exists():
            raise UserError(f'{new_wt} already exists')


def _watermark_renames(git: Git, old: str, new: str) -> list[tuple[str, str]]:
    """The goal's ``goal sync`` watermark refs, paired with their new names."""
    prefix = f'refs/chimera/synced/{old}/'
    return [
        (ref, f'refs/chimera/synced/{new}/{ref.removeprefix(prefix)}')
        for ref in git('for-each-ref', '--format=%(refname)', prefix).split()
    ]


def _rename_markers(git: Git, old: str, new: str) -> None:
    """Carry any transient append-in-progress markers over to the new goal name."""
    appending = (
        Path(git('rev-parse', '--path-format=absolute', '--git-common-dir').strip())
        / 'chimera'
        / 'appending'
    )
    if appending.is_dir():
        for marker in appending.glob(f'{old}{SEP}*'):
            # relative_to, not .name: a nested goal ('a/b') puts markers in a subdir
            mover = marker.relative_to(appending).as_posix().removeprefix(f'{old}{SEP}')
            target = appending / f'{new}{SEP}{mover}'
            target.parent.mkdir(parents=True, exist_ok=True)
            marker.rename(target)


def _remote_warnings(git: Git, old: str, actors: list[str]) -> list[str]:
    return [
        f'remote branch {remote}/{branch(old, actor)} keeps the old name — '
        f'rename or delete it on the remote yourself'
        for remote in git('remote').split()
        for actor in actors
        if git.ref_exists(f'refs/remotes/{remote}/{branch(old, actor)}')
    ]


def _upstream_warnings(git: Git, renames: list[tuple[str, str]]) -> list[str]:
    """A renamed branch whose upstream still names the old branch on the remote.

    ``git branch -m`` migrates the ``branch.<name>.*`` config section, so the upstream
    survives the rename — still pointing at the old-name ref on the remote, consistent
    with the remote being left alone. Flagged because a plain ``git push`` would now
    error (or quietly target the old name) until the remote side is renamed too.
    """
    warnings: list[str] = []
    for old_ref, new_ref in renames:
        try:
            merge = git('config', '--get', f'branch.{new_ref}.merge').strip()
        except GitError:
            continue
        if merge == f'refs/heads/{old_ref}':
            warnings.append(
                f'{new_ref} upstream still tracks {old_ref} on the remote — once renamed '
                f'there: git branch -u <remote>/{new_ref} {new_ref}'
            )
    return warnings


def _cwd_moved_to(cwd: Path | None, moved: list[tuple[Path, Path]]) -> Path | None:
    """Where ``cwd`` now lives, when a worktree move carried it along."""
    if cwd is None:
        return None
    resolved = cwd.resolve()
    for old_wt, new_wt in moved:
        if resolved.is_relative_to(old_wt.resolve()):
            return new_wt / resolved.relative_to(old_wt.resolve())
    return None
