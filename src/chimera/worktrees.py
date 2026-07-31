import os
from dataclasses import dataclass
from pathlib import Path
from subprocess import DEVNULL, PIPE, run

from giterator import GitError
from loguru import logger

from chimera.config import UserError
from chimera.git import Git

AGENT = 'agent'
HUMAN = 'human'
ACTORS = (HUMAN, AGENT)
"""The known actor names (completion candidates): ``human`` on a bare branch, ``agent`` in a worktree."""

DEFAULT_ACTORS = (AGENT,)
"""Actors created for a new goal. Only the agent gets a branch+worktree up front; ``human`` (and any
ad-hoc ``reviewer``/``pr``) are materialised on demand by ``goal sync`` — a spike never accrues a dead
branch."""

SEP = '@'
"""Joins goal and actor in a flat worktree dir / session name.

The branch uses ``/`` (``<goal>/<actor>``), but a dir can't nest there and goals
are kebab-case, so a dash would blur the boundary (``my-goal-agent``). ``@`` is the
only safe seam across every filesystem, shell, tmux and git GUI — it can't appear in
a goal or actor slug, so ``rsplit(SEP, 1)`` always recovers the pair.
"""


def _require_valid_name(name: str, kind: str, example: str, ref: str) -> str:
    """``name`` when the naming grammar can hold it as a ``kind``; ``UserError`` otherwise."""
    if SEP in name:
        raise UserError(f'{name!r} is not a valid {kind} name: {SEP!r} separates goal from actor')
    if '/' in name or '\\' in name:
        raise UserError(
            f'{name!r} is not a valid {kind} name: '
            f'no path separators — {kind} names are single path segments, like {example}'
        )
    if run(('git', 'check-ref-format', ref), stdout=DEVNULL, stderr=DEVNULL).returncode:
        raise UserError(f'{name!r} is not a valid {kind} name')
    return name


def require_valid_goal(name: str) -> str:
    """``name`` when it can be a goal, returned for chaining; ``UserError`` otherwise.

    The grammar (branch ``<goal>/<actor>``, worktree dir ``<goal>@<actor>``) can only hold a
    single path segment — a ``/`` would nest (or, as ``..``, escape) the worktrees dir, and
    ``@`` is the dir separator — that git accepts as a ref component (``git check-ref-format``
    is the authority, so ``..``, whitespace, control characters etc. are its rules, not ours).
    Every seam where a goal name *enters* the system (an explicit ``-g``, a new-goal
    positional) must call this before the name reaches :func:`branch`/:func:`worktree_path`.
    """
    return _require_valid_name(name, 'goal', "'feature-x' or 'pr-123'", f'refs/heads/{name}/actor')


def require_valid_actor(name: str) -> str:
    """``name`` when it can be an actor, returned for chaining; ``UserError`` otherwise.

    Same grammar and seam rule as :func:`require_valid_goal`, for the actor side of the pair.
    """
    return _require_valid_name(name, 'actor', "'agent' or 'reviewer'", f'refs/heads/goal/{name}')


def branch(goal: str, actor: str) -> str:
    """The branch name for an actor on a goal: ``<goal>/<actor>``."""
    return f'{goal}/{actor}'


def worktree_path(root: Path, goal: str, actor: str) -> Path:
    """The worktree directory for an actor on a goal: ``<root>/<goal>@<actor>``."""
    return root / f'{goal}{SEP}{actor}'


def session_name(project: str, goal: str, actor: str) -> str:
    """The agent session label: ``<project>@<goal>@<actor>``."""
    return SEP.join((project, goal, actor))


def worktree_actor(cwd: Path, worktrees: Path) -> str | None:
    """The actor whose ``<goal>@<actor>`` worktree physically holds ``cwd``, else ``None``.

    Trusts the directory, not a branch or a caller's assumption — the true actor (a
    ``reviewer``'s worktree, say) rather than a generic default. ``None`` when ``cwd``
    isn't inside one of ``worktrees``' ``<goal>@<actor>`` dirs.
    """
    worktrees = worktrees.resolve()
    cwd = cwd.resolve()
    if cwd != worktrees and worktrees not in cwd.parents:
        return None
    head = cwd.relative_to(worktrees).parts
    if not head or SEP not in head[0]:
        return None
    return head[0].split(SEP, 1)[1]


def worktree_dirs(root: Path) -> list[Path]:
    """Worktree directories present under root, sorted."""
    return sorted(child for child in root.iterdir() if child.is_dir()) if root.is_dir() else []


def goals(root: Path) -> set[str]:
    """Goal names present under root, derived from each goal's ``<goal>@agent`` worktree."""
    suffix = f'{SEP}{AGENT}'
    return {d.name.removesuffix(suffix) for d in worktree_dirs(root) if d.name.endswith(suffix)}


def goal_branch_actors(git: Git, goal: str) -> set[str]:
    """Actors with a ``<goal>/<actor>`` branch. Empty for a goal that doesn't exist.

    An actor is a single branch segment, so a nested goal is never mistaken for an
    actor of its parent: ``parent`` sees ``parent/agent`` (actor ``agent``) but not
    ``parent/child/agent`` (that is goal ``parent/child``'s, not an actor ``child/agent``
    of ``parent``).
    """
    branch_prefix = f'{goal}/'
    return {
        actor
        for b in git.branches()
        if b.startswith(branch_prefix) and '/' not in (actor := b.removeprefix(branch_prefix))
    }


def goal_actors(git: Git, root: Path, goal: str) -> set[str]:
    """Every actor in a goal's namespace: :func:`goal_branch_actors` unioned with those
    of its ``<goal>@<actor>`` worktree dirs.

    So cleanup sweeps up any actor beyond the default ``human``/``agent`` pair — a
    branch with no worktree (a human-style actor) and a worktree whose branch is gone
    both surface. Empty for a goal that doesn't exist.
    """
    dir_prefix = f'{goal}{SEP}'
    return goal_branch_actors(git, goal) | {
        d.name.removeprefix(dir_prefix)
        for d in worktree_dirs(root)
        if d.name.startswith(dir_prefix)
    }


def registered_worktrees(git: Git) -> set[Path]:
    """The worktree paths git knows about for repo, resolved."""
    out = git('worktree', 'list', '--porcelain')
    return {
        Path(line.removeprefix('worktree ')).resolve()
        for line in out.splitlines()
        if line.startswith('worktree ')
    }


def checkout_of(git: Git, ref: str) -> Path | None:
    """The worktree that currently has branch ``ref`` checked out, or ``None`` if none does.

    Parses ``git worktree list --porcelain`` (sibling to :func:`registered_worktrees`): a
    fast-forward of a checked-out branch must move its work tree too, not just the ref, so sync
    needs to know where — and a bare (never-checked-out) branch returns ``None`` so it can be
    repointed directly with ``git branch -f``.
    """
    current: Path | None = None
    for line in git('worktree', 'list', '--porcelain').splitlines():
        if line.startswith('worktree '):
            current = Path(line.removeprefix('worktree ')).resolve()
        elif line == f'branch refs/heads/{ref}':
            return current
    return None


@dataclass(frozen=True)
class Checkout:
    """The result of trying to land ``branch`` in the working checkout at ``where``."""

    done: bool  # True: HEAD moved onto the branch; False: skipped because the checkout was dirty
    where: Path  # the checkout's top-level dir
    branch: str  # the branch we (tried to) check out
    was: str | None  # the branch HEAD was on before (``None`` when it was detached)


def is_goal_worktree(path: Path, head: str) -> bool:
    """Whether the checkout at ``path`` is a managed ``<goal>@<actor>`` worktree.

    The dir name alone can't say — a *plain* checkout may legitimately carry ``@`` in its
    name (``proj@2``) — so the name must also agree with ``head``, the branch checked out
    there (``'HEAD'`` when detached): managed means the name splits on the separator into
    the pair the branch joins — the same name↔branch trust rule doctor's worktree checks
    use. A detached checkout in a ``<goal>@<actor>``-shaped dir still counts as managed:
    what can't be identified must never be flipped.
    """
    goal, sep, actor = path.name.partition(SEP)
    if not sep or SEP in actor:
        return False  # '@' can't appear in a goal or actor, so this isn't the managed shape
    return head in (branch(goal, actor), 'HEAD')


def checkout_here(git: Git, branch_name: str, into: Path, log_as: str) -> Checkout | None:
    """Check ``branch_name`` out in the checkout containing ``into``, when that's safe.

    Lets a human who just materialised/advanced ``<goal>/human`` land *on* it in place. Returns
    ``None`` (a silent skip — the caller wasn't in a position to land the branch) when ``into``
    isn't inside a plain checkout of ``git``'s repo, is a managed ``<goal>@<actor>`` worktree
    (never flip an agent's HEAD), already has ``branch_name``, or the branch is checked out in
    another worktree (git would refuse). Returns ``Checkout(done=False, …)`` when the checkout is
    dirty, so the caller can surface a commit/stash hint. Otherwise moves HEAD, logging the
    before/after HEAD (keyed by the branch each side, ``'HEAD'`` when detached — see
    ``agent-docs/logging.md``), and returns ``Checkout(done=True, …)``.
    """
    try:
        top = Path(Git(into)('rev-parse', '--show-toplevel').strip()).resolve()
    except GitError:
        return None  # not inside a git repo at all
    if top not in registered_worktrees(git):
        return None  # a different repo (e.g. the workspace itself), not this project's
    wt = Git(top)
    was = wt('rev-parse', '--abbrev-ref', 'HEAD').strip()  # 'HEAD' when detached
    if is_goal_worktree(top, was):
        return None  # a managed <goal>@<actor> worktree — never flip an agent's HEAD
    if was == branch_name:
        return None  # already here
    elsewhere = checkout_of(git, branch_name)
    if elsewhere is not None and elsewhere != top:
        return None  # lives in another worktree — git would refuse to check it out here
    landed = None if was == 'HEAD' else was
    if is_dirty(top):
        return Checkout(done=False, where=top, branch=branch_name, was=landed)
    before = {was: wt.rev_parse('HEAD', short=False)}
    wt('checkout', branch_name)
    logger.bind(
        worktree=str(top),
        git={'before': before, 'after': {branch_name: wt.rev_parse('HEAD', short=False)}},
    ).info(f'{log_as}: refs')
    return Checkout(done=True, where=top, branch=branch_name, was=landed)


_PATHSPEC_LIMIT = 60_000
"""Total pathspec bytes above which the squash search stops scoping by path and replays
base's whole history: pathspecs ride argv (``git log`` has no ``--pathspec-from-file``),
and a branch touching enough files would overflow the OS argument limit."""


def _patch_ids(diffs: bytes) -> set[str]:
    """The set of patch-ids in a (possibly multi-commit) diff, one per commit it contains.

    Bytes in: diff text splices in blob content, which is never guaranteed UTF-8.
    """
    if not diffs.strip():
        return set()
    out = run(('git', 'patch-id', '--stable'), input=diffs, stdout=PIPE).stdout
    return {line.split()[0].decode() for line in out.splitlines() if line.strip()}


def is_merged(git: Git, ref: str, base: str) -> bool:
    """Whether ref's work is already contained in base — nothing is lost by deleting it.

    Ancestry alone misses squash- and rebase-merges (the original commits never land on base),
    so we fall back to patch equivalence: ref is merged when ref is reachable from base (a
    fast-forward or a real merge commit), or every commit unique to ref has an equivalent patch
    already on base (rebase-merge, or a single squashed commit — ``git cherry``, which computes
    the patch-ids inside git, so base's whole history never streams through this process), or
    ref's combined diff matches one commit on base (a squash-merge of several commits) — sought
    only among the base commits touching ref's own paths, so a busy base replays a sliver of
    its history, not every diff anyone landed. What diff text we do handle stays bytes
    throughout (:meth:`Git.raw`): blob content is never guaranteed UTF-8.
    """
    try:
        git('merge-base', '--is-ancestor', ref, base)
        return True
    except GitError:
        pass
    # '-' marks a commit whose patch base already carries; '+' one it doesn't — or an empty
    # commit, which has no patch to match, so '+'es are harmless when their patches are empty
    # (--root: a parentless commit's patch is its whole tree, never empty). Anything not
    # shaped '<mark> <sha>' is a warning stderr merged into the output — never cherry's.
    cherry = [
        line.split()
        for line in git('cherry', base, ref).splitlines()
        if line.startswith(('+ ', '- '))
    ]
    unmatched = (sha for mark, sha in cherry if mark == '+')
    if any(mark == '-' for mark, _ in cherry) and not any(
        git.raw('diff-tree', '--root', '--no-commit-id', '-p', sha).strip() for sha in unmatched
    ):
        return True
    mb = git('merge-base', base, ref).strip()
    paths = [
        b':(literal)' + p  # a file named ':…' or '*' must match itself, never parse as magic
        for p in git.raw('diff', '--name-only', '--no-renames', '-z', mb, ref).split(b'\0')
        if p
    ]
    if not paths:
        return False  # ref's tree is mb's own — no diff to seek on base
    scope = ('--', *map(os.fsdecode, paths)) if sum(map(len, paths)) < _PATHSPEC_LIMIT else ()
    base_ids = _patch_ids(
        git.raw('log', '-p', '--no-color', '--full-history', f'{mb}..{base}', *scope)
    )
    return next(iter(_patch_ids(git.raw('diff', mb, ref))), '') in base_ids


def is_dirty(worktree: Path) -> bool:
    """Whether the worktree has uncommitted or untracked changes."""
    return bool(Git(worktree)('status', '--porcelain').strip())


def default_branch(git: Git) -> str:
    """The repo's default branch name (e.g. ``main`` or ``master``).

    Prefers the remote's published default (``origin/HEAD``, set by clone); else the first of
    ``main``/``master`` existing as a local or remote-tracking ref; else falls back to ``main``.
    """
    try:
        return (
            git('symbolic-ref', '--short', 'refs/remotes/origin/HEAD')
            .strip()
            .removeprefix('origin/')
        )
    except GitError:
        pass
    for name in ('main', 'master'):
        if git.ref_exists(name) or git.ref_exists(f'origin/{name}'):
            return name
    return 'main'


def base_ref(git: Git) -> str | None:
    """Start point for new branches: newest-committed of local ``<default>`` and ``origin/<default>``.

    ``<default>`` is the repo's :func:`default_branch`. Ties (e.g. both at the same commit)
    favour local. Returns ``None`` if neither ref exists.
    """
    default = default_branch(git)
    newest: str | None = None
    newest_committed = -1
    for ref in (default, f'origin/{default}'):
        try:
            committed = int(git('log', '-1', '--format=%ct', ref).strip())
        except GitError:
            continue
        if committed > newest_committed:
            newest, newest_committed = ref, committed
    return newest


def fetch_origin(git: Git) -> None:
    """Fetch ``origin`` with prune so remote-tracking refs are current. No-op without an origin."""
    if 'origin' in git('remote').split():
        git('fetch', '--prune', 'origin')


def fetch_origin_or_offline(git: Git) -> None:
    """:func:`fetch_origin`, but a failure becomes a clean ``--offline`` hint.

    For commands that fetch as a freshness courtesy and take ``--offline`` to skip it — a dead
    network (fast now, thanks to ``chimera.git``'s timeouts) shouldn't end in a traceback when
    the run could proceed without the fetch.
    """
    try:
        fetch_origin(git)
    except GitError as error:
        raise UserError(
            f'fetching origin failed — check network, or re-run with --offline:\n{error}'
        ) from None
