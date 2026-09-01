import os
import shutil
import subprocess
from collections.abc import Generator, Iterator, Mapping
from functools import partial
from pathlib import Path

from giterator import GitError
from loguru import logger

from chimera.agents import BRANCHED

from chimera.archive import (
    archive,
    Archive,
    events_orphaned,
    migrate,
    needs_migration,
    repair_events,
)
from chimera.commands.agent import agents, reconcile
from chimera.commands.doctor.core import (
    Check,
    Exclusions,
    Finding,
    iter_project_dirs,
    project_repo,
    read_raw,
    write_config,
)
from chimera.commands.hook import install as hook_install
from chimera.commands.init import TEMPLATE
from chimera.git import Git
from chimera.worktrees import (
    AGENT,
    HUMAN,
    SEP,
    base_ref,
    branch,
    checkout_of,
    default_branch,
    fetch_origin,
    goals,
    is_dirty,
    is_merged,
    registered_worktrees,
    worktree_path,
)


class WorkspaceConfigCheck:
    """The workspace root carries config.yaml with kind: workspace."""

    name = 'workspace-config'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        raw = read_raw(workspace)
        if raw and 'repo' in raw:
            # repo: belongs to a project — never stamp kind: workspace onto it.
            yield Finding(
                self.name,
                f'{workspace}/config.yaml looks like a project (has repo:), not a workspace root',
                resolved=False,
                fixable=False,
            )
            return
        kind = raw.get('kind') if raw else None
        if kind == 'workspace':
            return
        if kind is not None:
            yield Finding(
                self.name,
                f'{workspace}/config.yaml has kind: {kind} at the workspace root',
                resolved=False,
                fixable=False,
            )
            return
        missing = 'missing' if raw is None else 'missing kind: workspace'
        message = f'{workspace}/config.yaml {missing}'
        fixing = fix and not exclude.matches(self.name, message)
        if fixing:
            write_config(workspace, {'kind': 'workspace', **(raw or {})})
        yield Finding(self.name, message, resolved=fixing, fixable=True)


def _gitignore_entries(path: Path) -> list[str]:
    """Non-blank, stripped lines of a .gitignore, [] if it's absent."""
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


class GitignoreCheck:
    """The workspace .gitignore carries every entry the current template ships."""

    name = 'gitignore'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        gitignore = workspace / '.gitignore'
        have = _gitignore_entries(gitignore)
        missing = [e for e in _gitignore_entries(TEMPLATE / '.gitignore') if e not in have]
        messages = {entry: f'{gitignore} missing {entry!r}' for entry in missing}
        fixing = {
            entry: fix and not exclude.matches(self.name, message)
            for entry, message in messages.items()
        }
        if writing := [entry for entry in missing if fixing[entry]]:
            text = gitignore.read_text() if gitignore.exists() else ''
            if text and not text.endswith('\n'):
                text += '\n'
            gitignore.write_text(text + ''.join(f'{e}\n' for e in writing))
        for entry in missing:
            yield Finding(self.name, messages[entry], resolved=fixing[entry], fixable=True)


class WorkspaceDirsCheck:
    """Every directory the current workspace template ships exists in the workspace.

    Reconciles workspaces created before a template dir was added (e.g. ``roles/``).
    Derived from the template itself, never a hand-kept list, so it can't drift.
    ``--fix`` creates the dir with a ``.gitkeep`` (matching what ``init`` ships), which
    the workspace-clean sweep then commits.
    """

    name = 'workspace-dirs'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        for template_dir in sorted(d for d in TEMPLATE.iterdir() if d.is_dir()):
            target = workspace / template_dir.name
            if target.is_dir():
                continue
            message = f'{target} missing'
            fixing = fix and not exclude.matches(self.name, message)
            if fixing:
                target.mkdir()
                (target / '.gitkeep').touch()
            yield Finding(self.name, message, resolved=fixing, fixable=True)


class CaptainCheck:
    """The workspace names its captain, and roles/captain/ carries directives for it.

    Naming a captain is schema completion, like ``WorkspaceConfigCheck``'s ``kind:`` —
    ``--fix`` writes the literal default (``captain: captain``) onto a config.yaml that
    predates the captain feature (creating one if there's none yet, same as that
    check's legacy-root case); it never invents a unique persona name, that stays a
    human's call. Writing its directives is a separate, unfixable choice: like
    ``ProjectConfigCheck``'s ambiguous-kind case, an empty ``roles/captain/`` is only
    ever reported — and only once a captain is actually named, so an unnamed captain's
    empty directives aren't also flagged as noise.
    """

    name = 'captain'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        raw = read_raw(workspace)
        if raw is None or 'captain' not in raw:
            message = (
                f'{workspace}/config.yaml has no captain: — '
                'the workspace has never named its captain persona'
            )
            fixing = fix and not exclude.matches(self.name, message)
            if fixing:
                write_config(workspace, {**(raw or {}), 'captain': 'captain'})
            yield Finding(self.name, message, resolved=fixing, fixable=True)
            return
        # Top-level only, mirroring the launch render: a *.md in a subdir is
        # structure, not payload, so it doesn't count as a directive here either.
        directives = workspace / 'roles' / 'captain'
        if not directives.is_dir() or not any(directives.glob('*.md')):
            yield Finding(
                self.name,
                f'{directives} has no *.md directive files for the captain role',
                resolved=False,
                fixable=False,
            )


class OccupancyWarningCheck:
    """No leftover ``hooks.occupancy_warning`` key survives in a workspace's config.yaml.

    An earlier, since-removed SessionStart double-occupancy warning (it fired on a
    harmless harness-native attach — resuming or watching a running background job
    from ``claude agents`` — as readily as a genuine second writer, so the check
    itself is gone, not just gated) was briefly gated behind this key. Nothing reads
    it anymore, so a config.yaml still carrying it from that attempt is dead config —
    ``--fix`` strips the key, and the ``hooks:`` block with it if it was the only entry.
    """

    name = 'occupancy-warning'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        raw = read_raw(workspace) or {}
        hooks = raw.get('hooks')
        if not isinstance(hooks, dict) or 'occupancy_warning' not in hooks:
            return
        message = f'{workspace}/config.yaml has a stray hooks.occupancy_warning — no longer read'
        fixing = fix and not exclude.matches(self.name, message)
        if fixing:
            remaining = {k: v for k, v in hooks.items() if k != 'occupancy_warning'}
            new_raw = {k: v for k, v in raw.items() if k != 'hooks'}
            if remaining:
                new_raw['hooks'] = remaining
            write_config(workspace, new_raw)
        yield Finding(self.name, message, resolved=fixing, fixable=True)


class ProjectConfigCheck:
    """Each project's config.yaml carries kind: project."""

    name = 'project-config'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        for project in iter_project_dirs(workspace):
            raw = read_raw(project) or {}
            kind = raw.get('kind')
            if kind == 'project':
                continue
            if 'repo' in raw:
                # repo: is the authoritative project signal — set the right kind,
                # dropping any wrong one (e.g. a stray kind: workspace).
                problem = (
                    'missing kind: project'
                    if kind is None
                    else f'has kind: {kind} but repo: marks it a project'
                )
                message = f'{project}/config.yaml {problem}'
                fixing = fix and not exclude.matches(self.name, message)
                if fixing:
                    write_config(
                        project,
                        {'kind': 'project', **{k: v for k, v in raw.items() if k != 'kind'}},
                    )
                yield Finding(self.name, message, resolved=fixing, fixable=True)
            elif kind is not None:
                yield Finding(
                    self.name,
                    f'{project}/config.yaml has unexpected kind: {kind}',
                    resolved=False,
                    fixable=False,
                )
            else:
                yield Finding(
                    self.name,
                    f'{project}/config.yaml has no kind and no repo',
                    resolved=False,
                    fixable=False,
                )


class RuntimeStateDirCheck:
    """Runtime state lives under ``state/`` — migrating the legacy ``logs/`` and ``comms/``.

    The action log, session archive, rendered launch contexts and mailboxes all sit under
    one gitignored ``state/`` dir. Older workspaces kept the log (and ``context/``) under
    ``logs/`` and the mail under ``comms/``; ``--fix`` renames them into place — ``logs/`` →
    ``state/`` (its ``chimera.jsonl`` → ``state/log.jsonl``) and ``comms/`` → ``state/mail/``.
    Clean-only: a collision (the target already exists) is reported for a human to merge
    rather than clobbered.
    """

    name = 'state-dir'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        state = workspace / 'state'
        legacy_log = workspace / 'logs'
        if legacy_log.is_dir():
            if state.exists():
                yield Finding(
                    self.name,
                    f'legacy logs/ and state/ both exist under {workspace} — merge by hand',
                    resolved=False,
                    fixable=False,
                )
            else:
                message = 'legacy logs/ → state/ (chimera.jsonl → log.jsonl)'
                fixing = fix and not exclude.matches(self.name, message)
                if fixing:
                    legacy_log.rename(state)
                    jsonl = state / 'chimera.jsonl'
                    if jsonl.exists():
                        jsonl.rename(state / 'log.jsonl')
                yield Finding(self.name, message, resolved=fixing, fixable=True)
        legacy_mail = workspace / 'comms'
        if legacy_mail.is_dir():
            mail = state / 'mail'
            if mail.exists():
                yield Finding(
                    self.name,
                    f'legacy comms/ and state/mail/ both exist under {workspace} — merge by hand',
                    resolved=False,
                    fixable=False,
                )
            else:
                message = 'legacy comms/ → state/mail/'
                fixing = fix and not exclude.matches(self.name, message)
                if fixing:
                    state.mkdir(parents=True, exist_ok=True)
                    legacy_mail.rename(mail)
                yield Finding(self.name, message, resolved=fixing, fixable=True)


def _persona(raw: Mapping[str, object] | None) -> str | None:
    """The captain's persona from a raw workspace config, tolerating any shape.

    ``captain:`` is either a name or a mapping carrying one (see
    :class:`~chimera.config.CaptainConfig`). Anything else answers ``None``: this feeds a
    rename, and a config too odd to read simply means no chat row is recognised as the
    captain's — never an exception out of the command that repairs the workspace.
    """
    captain = (raw or {}).get('captain')
    if isinstance(captain, str):
        return captain
    if isinstance(captain, Mapping):
        name = captain.get('name')
        return name if isinstance(name, str) else None
    return None


class ArchiveSchemaCheck:
    """The session archive is on the current schema — migrating a pre-trim database.

    The archive once carried searchable history, cost and summaries beside identity;
    those moved out (agentsview does them better), ``name`` became ``address``, and
    ``addressable``/``harness_version`` arrived. ``--fix`` rebuilds the database in place
    (see :func:`chimera.archive.migrate`), keeping every session and every event.

    The migration also applies the address rule retroactively: a claim survives only where
    the old ``manager`` column proves a launcher stamped the session, or the axes name a
    goal worktree. Claims inferred from geography alone are dropped — geography never
    entitled a session to an address, and grandfathering them would leave rows the current
    code would refuse to write.

    The check also catches an archive a *previous* rebuild left with an ``events`` table
    referencing a table that is gone. That state is silent — the ``sessions`` columns look
    current, so the schema itself reports healthy — while every append to the timeline
    fails, which takes the hooks, the heartbeat and reconciliation down with it. Asking
    the question is a schema read, so it costs nothing to keep asking long after the
    rebuild that could cause it was fixed.
    """

    name = 'archive-schema'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        path = workspace / 'state' / 'archive.db'
        # the captain's persona is how a pre-grammar chat row is recognised as the
        # captain's, so the rename can carry its address forward instead of dropping it.
        # Read tolerantly: doctor runs on workspaces too broken to have a valid config
        persona = _persona(read_raw(workspace))
        for broken, message, repair, counting in (
            (
                needs_migration,
                'predates the trimmed session schema',
                partial(migrate, captain=persona),
                'sessions',
            ),
            (events_orphaned, 'has events pointing at a dropped table', repair_events, 'events'),
        ):
            if not broken(path):
                continue
            detail = f'{path} {message}'
            fixing = fix and not exclude.matches(self.name, detail)
            if fixing:
                logger.bind(path=str(path), **{counting: repair(path)}).info(
                    'doctor: archive rebuilt'
                )
            yield Finding(self.name, detail, resolved=fixing, fixable=True)


class OpenSessionCheck:
    """Sessions the archive still shows open that no harness reports running.

    A session that dies without its end hook firing — killed, crashed, its machine
    rebooted — stays open forever, and an open row outranks the closed ones a resume
    chooses between. :func:`~chimera.commands.agent.reconcile` fixes exactly this, but
    only ever ran as a side effect of a *lister*: undiscoverable, and silently skipped by
    anyone who reaches for ``ch doctor`` when something looks wrong. On a real workspace
    that left 57 rows of 271 open, 50 of them long dead. Repair belongs in the repair
    command.

    ``--fix`` closes them, through the same machinery and with the same refusal: a
    harness that cannot be consulted is not a harness reporting nothing, so with
    ``claude`` off the PATH this closes nothing rather than declaring the machine empty.
    Reported but never fixed alongside them are rows whose transcript the harness has
    since pruned — unresumable, but that is claude's retention talking, not damage, and
    nothing here can or should undo it.
    """

    name = 'open-sessions'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        path = workspace / 'state' / 'archive.db'
        if not path.exists() or needs_migration(path) or events_orphaned(path):
            return  # the schema checks above own this; nothing here can read it yet
        listing = agents()
        with archive(workspace) as store:
            live = {session.id for session in listing}
            open_rows = [row for row in store.sessions(active=True) if row.native_id not in live]
        if not open_rows:
            return
        message = f'{len(open_rows)} archived sessions are open that no harness reports'
        fixing = fix and not exclude.matches(self.name, message)
        # resolved by what reconcile *did*, never by having called it: it declines when a
        # harness cannot be consulted, and a check reporting a repair it did not make is
        # worse than one reporting none
        closed = reconcile(workspace, listing) if fixing else []
        yield Finding(self.name, message, resolved=len(closed) == len(open_rows), fixable=True)


class HarnessContractCheck:
    """Recorded sessions still behave the way ``agent-docs/sessions.md`` says they do.

    Almost everything chimera knows about a harness is *observed*, not promised, so it
    will drift — and drift that nobody notices is the expensive kind. This re-asserts the
    load-bearing claims against sessions already recorded, which costs a SQL read and no
    model turn, and so can run on every doctor:

    - **the transcript is named after the session.** Identity anchors on the transcript
      stem precisely because that is documented *and* definitionally resumable; a row
      whose stem disagrees with its id means that stopped being true, and resume would be
      handing out ids the harness no longer knows.
    - **a branched session has a plausible parent.** A fork inherits its address from the
      session it was split off, presumed by cwd. One with no other session ever recorded
      in that directory means the presumption had nothing to work with.

    Findings are never fixable: each one says a harness changed under us, which is a
    human's to read and act on, not doctor's to paper over. Versions are reported too —
    every session records the build that produced it, so a version this doc has never
    validated is itself worth seeing.
    """

    name = 'harness-contract'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        path = workspace / 'state' / 'archive.db'
        if not path.exists() or needs_migration(path):
            return  # nothing to check, or the schema check has the floor
        with Archive.open(path) as store:
            sessions = store.sessions()
            branched = {event.native_id for event in store.events(kind=BRANCHED)}
        by_cwd: dict[Path, int] = {}
        for session in sessions:
            if session.cwd is not None:
                by_cwd[session.cwd] = by_cwd.get(session.cwd, 0) + 1
        for session in sessions:
            if session.transcript is not None and session.transcript.stem != session.native_id:
                yield Finding(
                    self.name,
                    f'{session.native_id}: transcript is named {session.transcript.stem}, '
                    f'not the session — identity no longer anchors on it',
                    resolved=False,
                    fixable=False,
                )
            if (
                session.native_id in branched
                and session.cwd is not None
                and by_cwd.get(session.cwd, 0) < 2
            ):
                yield Finding(
                    self.name,
                    f'{session.native_id}: branched, but no other session was ever recorded '
                    f'in {session.cwd} — it can have inherited nothing',
                    resolved=False,
                    fixable=False,
                )
        logger.bind(
            sessions=len(sessions),
            versions=sorted({s.harness_version for s in sessions if s.harness_version}),
        ).info('doctor: harness contract checked')


class StaleHumanWorktreeCheck:
    """Legacy {goal}-human worktrees are gone; the human branch survives bare."""

    name = 'human-worktrees'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        for project in iter_project_dirs(workspace):
            repo = project_repo(project)
            worktrees_dir = project / 'worktrees'
            if repo is None or not repo.is_dir() or not worktrees_dir.is_dir():
                continue
            git = Git(repo)
            registered = registered_worktrees(git)
            branches = set(git.branches())
            base = base_ref(git)
            for worktree in sorted(worktrees_dir.glob('*-human')):
                if not worktree.is_dir() or worktree.resolve() not in registered:
                    continue  # a leftover dir, not a real worktree — orphan check covers it
                branch = f'{worktree.name.removesuffix("-human")}/human'
                dirty = is_dirty(worktree)
                unmerged = branch in branches and not (
                    base is not None and is_merged(git, branch, base)
                )
                if dirty or unmerged:
                    reason = 'uncommitted changes' if dirty else 'unmerged commits'
                    yield Finding(
                        self.name,
                        f'{worktree} has {reason} — left in place',
                        resolved=False,
                        fixable=False,
                    )
                    continue
                message = f'stale human worktree {worktree}'
                fixing = fix and not exclude.matches(self.name, message)
                if fixing:
                    git('worktree', 'remove', str(worktree))
                yield Finding(self.name, message, resolved=fixing, fixable=True)


class InertBranchCheck:
    """A goal's non-agent actor branch sitting at an already-integrated commit is dead weight.

    The agent branch always has a worktree and keeps advancing; a ``human`` (or ad-hoc
    ``reviewer``/``pr``) branch is lazy in the current layout — but earlier ``goal start`` created
    ``<goal>/human`` up front, most commonly parked at the branch point. Such a branch carries
    nothing not already recoverable elsewhere, so it's inert: ``--fix`` deletes it (the human can
    re-materialise it any time with ``goal sync``), logging the sha first for recovery.

    Inert means the tip is either pushed (contained in a remote-tracking ref) or an ancestor of the
    local default branch — nothing unique to the branch is lost. Only branches of a *known* goal
    (one with a ``<goal>@agent`` worktree) are considered, and a branch checked out anywhere is left
    alone (git won't force-delete it, and it may be where a human is standing).
    """

    name = 'inert-branches'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        for project in iter_project_dirs(workspace):
            repo = project_repo(project)
            worktrees_dir = project / 'worktrees'
            if repo is None or not repo.is_dir() or not worktrees_dir.is_dir():
                continue
            git = Git(repo)
            branches = set(git.branches())
            default = default_branch(git)
            default_ref = default if default in branches else None
            for goal in sorted(goals(worktrees_dir)):
                for ref in sorted(b for b in branches if b.startswith(f'{goal}/')):
                    actor = ref.removeprefix(f'{goal}/')
                    if actor == AGENT or '/' in actor:
                        continue  # the agent has a worktree; anything nested isn't an actor branch
                    if checkout_of(git, ref) is not None:
                        continue  # checked out somewhere — never force-delete under a checkout
                    if not _is_inert(git, ref, default_ref):
                        continue
                    message = f'{ref} points at an already-integrated commit — inert'
                    fixing = fix and not exclude.matches(self.name, message)
                    if fixing:
                        with git.ref_log(f'{self.name}: refs', ref):
                            git('branch', '-D', ref)
                    yield Finding(self.name, message, resolved=fixing, fixable=True)


def _is_inert(git: Git, ref: str, default_ref: str | None) -> bool:
    """Whether ``ref``'s tip is recoverable elsewhere — pushed, or an ancestor of the default branch.

    Either guarantee means deleting the branch loses nothing: the commit lives on a remote, or on
    the local default branch it was taken from. Without a remote and without the default branch,
    neither can be proven, so the branch is treated as *not* inert (never deleted).

    The pushed test asks for one commit reachable from ``ref`` but from no remote-tracking ref —
    none means the tip is on a remote. ``branch --remotes --contains`` answers the same question
    but names the containing branches, which costs seconds per call in a repo with thousands of
    remote-tracking refs; we only need the boolean.
    """
    if not git('rev-list', '-1', ref, '--not', '--remotes', '--').strip():
        return True  # pushed — recoverable from a remote
    return default_ref is not None and _is_ancestor(git, ref, default_ref)


class LegacyWorktreeSeparatorCheck:
    """Agent worktree dirs use <goal>@<actor>, migrating the legacy <goal>-<actor>."""

    name = 'worktree-separator'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        for project in iter_project_dirs(workspace):
            repo = project_repo(project)
            worktrees_dir = project / 'worktrees'
            if repo is None or not repo.is_dir() or not worktrees_dir.is_dir():
                continue
            git = Git(repo)
            root = worktrees_dir.resolve()
            for worktree in sorted(p for p in registered_worktrees(git) if p.parent == root):
                canonical = _canonical_worktree(worktree)
                if canonical is None or canonical == worktree:
                    continue
                message = f'legacy worktree {worktree.name} → {canonical.name}'
                fixing = fix and not exclude.matches(self.name, message)
                if fixing:
                    git('worktree', 'move', str(worktree), str(canonical))
                yield Finding(self.name, message, resolved=fixing, fixable=True)


def _canonical_worktree(worktree: Path) -> Path | None:
    """Where worktree should live given its <goal>/<actor> branch; None if it has none.

    The branch is trusted only when it is exactly ``<goal>/<actor>`` — neither segment
    may contain a slash, so a nested-prefix branch (``parked/<goal>/<actor>``) is never
    misread as goal ``parked/<goal>``, which would "canonicalise" the worktree into a
    subdirectory. A missing dir (a stale registration) is the orphaned-worktrees check's
    concern. Human worktrees return None too — the human-worktrees check removes rather
    than renames them.
    """
    if not worktree.is_dir():
        return None
    branch = Git(worktree)('rev-parse', '--abbrev-ref', 'HEAD').strip()
    match branch.split('/'):
        case [goal, actor] if actor != HUMAN:
            return worktree_path(worktree.parent, goal, actor)
    return None


class WorktreeBranchCheck:
    """Each agent worktree is on the branch its <goal>@<actor> name implies.

    The inverse of the separator check: that one trusts the branch and fixes the dir
    name; this one trusts the dir name and fixes the branch. Catches a git GUI flipping
    a worktree onto the wrong branch (or detaching its HEAD) — the dir still says which
    branch belongs here. ``--fix`` checks the right branch back out, but only when the
    worktree is clean (a dirty switch could lose uncommitted work); the before/after
    HEAD shas are logged first so the move can be undone (see ``agent-docs/logging.md``).

    When the implied branch is *gone* (its goal was finished after the work moved to
    another branch — e.g. parked under a prefix), the worktree is a leftover: ``--fix``
    removes it, but only when it is clean and on a real branch, so every commit stays
    reachable from the branch it sits on. A dirty or detached leftover needs a human.
    """

    name = 'worktree-branch'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        for project in iter_project_dirs(workspace):
            repo = project_repo(project)
            worktrees_dir = project / 'worktrees'
            if repo is None or not repo.is_dir() or not worktrees_dir.is_dir():
                continue
            git = Git(repo)
            branches = set(git.branches())
            root = worktrees_dir.resolve()
            for worktree in sorted(p for p in registered_worktrees(git) if p.parent == root):
                yield from self._check(git, worktree, branches, fix, exclude)

    def _check(
        self, git: Git, worktree: Path, branches: set[str], fix: bool, exclude: Exclusions
    ) -> Iterator[Finding]:
        if not worktree.is_dir() or SEP not in worktree.name:
            return  # a stale registration (orphan check's concern) or an unmanaged dir
        goal, actor = worktree.name.rsplit(SEP, 1)
        if actor == HUMAN:
            return  # humans don't get worktrees — not this check's business
        wt = Git(worktree)
        actual = wt('rev-parse', '--abbrev-ref', 'HEAD').strip()  # 'HEAD' when detached
        expected = branch(goal, actor)
        if actual == expected:
            return
        if expected not in branches:
            yield from self._leftover(git, wt, worktree, actual, expected, fix, exclude)
            return
        if is_dirty(worktree):
            yield Finding(
                self.name,
                f'{worktree} is on {actual}, expected {expected} — uncommitted changes, left in place',
                resolved=False,
                fixable=False,
            )
            return
        message = f'{worktree} is on {actual}, expected {expected}'
        fixing = fix and not exclude.matches(self.name, message)
        if fixing:
            before = {actual: wt.rev_parse('HEAD', short=False)}
            wt('checkout', expected)
            logger.bind(
                worktree=str(worktree),
                git={'before': before, 'after': {expected: wt.rev_parse('HEAD', short=False)}},
            ).info(f'{self.name}: refs')
        yield Finding(self.name, message, resolved=fixing, fixable=True)

    def _leftover(
        self,
        git: Git,
        wt: Git,
        worktree: Path,
        actual: str,
        expected: str,
        fix: bool,
        exclude: Exclusions,
    ) -> Iterator[Finding]:
        """Findings for a worktree whose implied branch no longer exists.

        Removal is only safe when every commit stays reachable afterwards: the worktree
        must be clean and on a real branch (a detached HEAD's commits may be referenced
        by nothing else). No ref changes, but the branch and sha the worktree held are
        logged so it can be recreated (``git worktree add <dir> <branch>``).
        """
        gone = f'{worktree} is on {actual}, but branch {expected} is gone'
        if actual == 'HEAD':
            yield Finding(
                self.name, f'{gone} — detached, left in place', resolved=False, fixable=False
            )
            return
        if is_dirty(worktree):
            yield Finding(
                self.name,
                f'{gone} — uncommitted changes, left in place',
                resolved=False,
                fixable=False,
            )
            return
        message = f'{gone} — leftover worktree'
        fixing = fix and not exclude.matches(self.name, message)
        if fixing:
            sha = wt.rev_parse('HEAD', short=False)
            git('worktree', 'remove', str(worktree))
            logger.bind(worktree=str(worktree), branch=actual, sha=sha).info(
                f'{self.name}: removed'
            )
        yield Finding(self.name, message, resolved=fixing, fixable=True)


class OrphanedWorktreeCheck:
    """Git's worktree registrations and the worktrees/ dir agree with each other.

    ``git worktree prune`` sweeps every stale registration in one go, so excluding
    one only mutes its report — the prune run for the others still removes it.
    """

    name = 'orphaned-worktrees'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        for project in iter_project_dirs(workspace):
            repo = project_repo(project)
            if repo is None or not repo.is_dir():
                continue
            git = Git(repo)
            registered = registered_worktrees(git)
            stale = sorted(path for path in registered if not path.exists())
            if stale:
                messages = {path: f'stale worktree registration for {path}' for path in stale}
                fixing = fix and not all(
                    exclude.matches(self.name, message) for message in messages.values()
                )
                if fixing:
                    git('worktree', 'prune')
                for path in stale:
                    yield Finding(self.name, messages[path], resolved=fixing, fixable=True)
            worktrees_dir = project / 'worktrees'
            if worktrees_dir.is_dir():
                for child in sorted(worktrees_dir.iterdir()):
                    if child.is_dir() and child.resolve() not in registered:
                        yield Finding(
                            self.name,
                            f'{child} is not a registered worktree',
                            resolved=False,
                            fixable=False,
                        )


def chimera_repo(start: Path = Path(__file__)) -> Path | None:
    """The git checkout chimera itself is running from, None for a git-less install.

    Resolved via Python's own module location, not the installed package's path: for an
    editable install (the normal case for a dev checkout — ``uv tool install --editable``
    or ``uv run`` from a worktree), that location *is* the original checkout, not a copy
    under ``site-packages``, so walking up from it finds the real ``.git``. A non-editable
    install (built/copied into ``site-packages``) has no ``.git`` nearby, so this returns
    None and the check that uses it goes quiet. Takes ``start`` as a parameter, rather than
    hardcoding ``__file__`` inside, so the walk can be exercised against a throwaway
    directory tree in tests.
    """
    for directory in start.resolve().parents:
        if (directory / '.git').exists():
            return directory
    return None


def _is_ancestor(git: Git, ancestor: str, descendant: str) -> bool:
    """Whether ancestor's history is fully contained in descendant — a true fast-forward."""
    try:
        git('merge-base', '--is-ancestor', ancestor, descendant)
        return True
    except GitError:
        return False


def _repoint(git: Git, branch_name: str, target: str) -> str | None:
    """Force branch_name to target via ``git branch -f``; its new sha, None if blocked.

    None means git refused — the branch is checked out somewhere (this repo or another
    worktree of it), so moving it could strand that checkout. Never raises: that's a routine,
    expected outcome here, not a bug.
    """
    try:
        git('branch', '-f', branch_name, target)
    except GitError:
        return None
    return git.rev_parse(branch_name, short=False)


def _worktree_on(git: Git, branch_name: str) -> Path | None:
    """The worktree (of git's repo) that has branch_name checked out, None if none does."""
    here: Path | None = None
    for line in git('worktree', 'list', '--porcelain').splitlines():
        if line.startswith('worktree '):
            here = Path(line.removeprefix('worktree '))
        elif line == f'branch refs/heads/{branch_name}':
            return here
    return None


def _advance_checkout(git: Git, branch_name: str, target: str) -> str | None:
    """Fast-forward branch_name's own checkout up to target; its new sha, None if blocked.

    The fallback when ``_repoint`` (``git branch -f``) refuses because branch_name is checked
    out — a checkout can't be force-moved, but a clean one can ``merge --ff-only`` to advance
    both its branch and its working tree. None if no worktree holds it, that worktree is dirty,
    or target isn't a fast-forward of branch_name (a diverged branch needs a human). Never
    raises: a blocked move is a routine, expected outcome here.
    """
    worktree = _worktree_on(git, branch_name)
    if worktree is None or is_dirty(worktree):
        return None
    checkout = Git(worktree)
    try:
        checkout('merge', '--ff-only', target)
    except GitError:
        return None
    return checkout.rev_parse('HEAD', short=False)


class ChimeraUpToDateCheck:
    """Chimera's own checkout is current with origin, and any deploy branch tracks main.

    Skipped entirely when chimera isn't running from a git checkout. ``git fetch`` always
    runs, even on a plain check — it's read-only. ``--fix`` fast-forwards the default branch
    to ``origin/<default>`` once ancestry confirms it's a true fast-forward — local already
    containing everything origin has needs no action, and a divergent history needs a human,
    so both are left untouched (reported, not fixed, in the divergent case). Only once the
    default branch is confirmed current does a ``deploy`` branch, if one exists, get checked
    — and if needed, repointed — against it. A deploy branch checked out somewhere (the normal
    state of a dedicated deploy clone, where ``git branch -f`` can't move it) is instead
    fast-forwarded in place, provided that checkout is clean and the move is a true fast-forward.
    """

    name = 'chimera-up-to-date'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        repo = chimera_repo()
        logger.bind(repo=str(repo) if repo else None).info(f'{self.name}: checkout')
        if repo is None:
            return
        git = Git(repo)
        fetch_origin(git)
        default = default_branch(git)
        try:
            local_sha = git.rev_parse(default, short=False)
            remote_sha = git.rev_parse(f'origin/{default}', short=False)
        except GitError:
            return  # no local/remote-tracking branch to compare — nothing to verify
        current = yield from self._sync_default(
            git, repo, default, local_sha, remote_sha, fix, exclude
        )
        if current is None:
            return  # default branch needs a human before deploy can be trusted against it
        yield from self._sync_deploy(git, repo, default, current, fix, exclude)

    def _sync_default(
        self,
        git: Git,
        repo: Path,
        default: str,
        local_sha: str,
        remote_sha: str,
        fix: bool,
        exclude: Exclusions,
    ) -> Generator[Finding, None, str | None]:
        """Findings about the default branch; returns its sha once confirmed current."""
        if local_sha == remote_sha:
            return local_sha
        remote = f'origin/{default}'
        if _is_ancestor(git, remote_sha, local_sha):
            return local_sha  # local already contains everything origin has — nothing to catch up
        if not _is_ancestor(git, local_sha, remote_sha):
            yield Finding(
                self.name,
                f'{repo} {default} has diverged from {remote} — needs a human to merge',
                resolved=False,
                fixable=False,
            )
            return None
        behind = f'{repo} {default} is behind {remote}'
        if not fix or exclude.matches(self.name, behind):
            yield Finding(self.name, behind, resolved=False, fixable=True)
            return None
        new_sha = _repoint(git, default, remote)
        if new_sha is None:
            yield Finding(
                self.name,
                f'{repo} {default} is behind {remote} — '
                'could not fast-forward, branch checked out elsewhere',
                resolved=False,
                fixable=True,
            )
            return None
        logger.bind(git={'before': {default: local_sha}, 'after': {default: new_sha}}).info(
            f'{self.name}: refs'
        )
        yield Finding(
            self.name, f'{repo} {default} fast-forwarded to {remote}', resolved=True, fixable=True
        )
        return new_sha

    def _sync_deploy(
        self, git: Git, repo: Path, default: str, current: str, fix: bool, exclude: Exclusions
    ) -> Iterator[Finding]:
        deploy_sha = git.rev_parse('deploy', short=False) if 'deploy' in git.branches() else None
        if deploy_sha is None or deploy_sha == current:
            return
        stale = f'{repo} deploy does not point at {default}'
        if not fix or exclude.matches(self.name, stale):
            yield Finding(self.name, stale, resolved=False, fixable=True)
            return
        new_sha = _repoint(git, 'deploy', default)
        if new_sha is None:  # deploy is checked out — branch -f can't move it, ff its checkout
            new_sha = _advance_checkout(git, 'deploy', default)
        if new_sha is None:
            yield Finding(
                self.name,
                f'{repo} deploy does not point at {default} — could not repoint; its checkout '
                'has uncommitted changes or has diverged from main, needs a human',
                resolved=False,
                fixable=True,
            )
            return
        logger.bind(git={'before': {'deploy': deploy_sha}, 'after': {'deploy': new_sha}}).info(
            f'{self.name}: refs'
        )
        yield Finding(
            self.name, f'{repo} deploy repointed to {default}', resolved=True, fixable=True
        )


class WorkspaceEnvCheck:
    """$CHIMERA_WORKSPACE is exported and points at this workspace."""

    name = 'workspace-env'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        export = f'export CHIMERA_WORKSPACE="{workspace}"'
        hint = 'add to your shell profile (~/.zshrc, ~/.bashrc, ~/.profile):'
        env = os.environ.get('CHIMERA_WORKSPACE')
        if env is None:
            yield Finding(
                self.name,
                f'$CHIMERA_WORKSPACE is not set — {hint}\n    {export}',
                resolved=False,
                fixable=False,
            )
        elif Path(env).expanduser().resolve() != workspace.resolve():
            yield Finding(
                self.name,
                f'$CHIMERA_WORKSPACE is {env}, not this workspace — {hint}\n    {export}',
                resolved=False,
                fixable=False,
            )


# Per supported shell: the script `ch --install-completion` writes (under home), the
# startup files that may carry a hand-managed eval line instead, and the typer-style
# source instruction for that line (the hint names the first startup file).
_COMPLETION_SHELLS = {
    'zsh': ('.zfunc/_ch', ('.zshrc', '.zshenv', '.zprofile'), 'source_zsh'),
    'bash': ('.bash_completions/ch.sh', ('.bashrc', '.bash_profile', '.profile'), 'source_bash'),
}


class ShellCompletionCheck:
    """Tab completion for ch is installed in the user's shell."""

    name = 'shell-completion'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        shell = Path(os.environ.get('SHELL', '')).name
        if shell not in _COMPLETION_SHELLS:
            return  # unknown or unsupported shell — nothing to verify
        script, rc_names, instruction = _COMPLETION_SHELLS[shell]
        home = Path.home()
        if (home / script).is_file():
            return  # installed by ch --install-completion
        if any(
            (rc := home / name).is_file() and '_CH_COMPLETE' in rc.read_text() for name in rc_names
        ):
            return  # hand-managed eval line
        yield Finding(
            self.name,
            f'tab completion for ch is not installed for {shell} — '
            f'run `ch --install-completion`, or add to ~/{rc_names[0]}:\n'
            f'    eval "$(env _CH_COMPLETE={instruction} ch)"',
            resolved=False,
            fixable=False,
        )


def fblog_installed() -> bool:
    """
    Whether the fblog binary is on the PATH — indirection so the CLI-level doctor tests
    (which run the real CHECKS tuple) can pin it, like ``chimera_repo``.
    """
    return shutil.which('fblog') is not None


class FblogCheck:
    """
    fblog — ``ch logtail``'s renderer — is installed; ``--fix`` brew-installs it.
    """

    name = 'fblog'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        if fblog_installed():
            return
        missing = "fblog (ch logtail's renderer) is not installed"
        if shutil.which('brew') is None:
            yield Finding(
                self.name,
                f'{missing} and brew is not available to install it — '
                'see https://github.com/brocode/fblog',
                resolved=False,
                fixable=False,
            )
            return
        if not fix or exclude.matches(self.name, missing):
            yield Finding(self.name, missing, resolved=False, fixable=True)
            return
        try:
            subprocess.run(['brew', 'install', 'fblog'], capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as error:
            yield Finding(
                self.name,
                f'{missing}; `brew install fblog` failed:\n{error.stderr.strip()}',
                resolved=False,
                fixable=True,
            )
            return
        yield Finding(
            self.name, 'fblog installed (brew install fblog)', resolved=True, fixable=True
        )


class ClaudeHooksCheck:
    """Chimera's capture + delivery hooks are installed in the user's Claude settings.

    User-wide (``~/.claude/settings.json``), so they fire for every session: SessionStart/End
    feed the archive, UserPromptSubmit delivers mail into the turn. ``--fix`` merges them in
    idempotently, preserving any existing hooks while sweeping superseded chimera spellings
    (a stale ``ch msg drain --inject`` would double-inject beside ``ch hook deliver``). It is
    the machine's Claude config, not the workspace's — so doctor *is* the installer (there is
    no ``ch hook install`` to forget).
    """

    name = 'claude-hooks'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        path = hook_install.settings_path()
        settings = hook_install.read(path)
        missing = hook_install.missing_hooks(settings)
        stale = hook_install.stale_hooks(settings)
        if not missing and not stale:
            return
        parts = [f'missing chimera hooks: {", ".join(missing)}'] if missing else []
        if stale:
            parts.append(f'superseded chimera hooks: {", ".join(stale)}')
        message = f'{path} {"; ".join(parts)}'
        fixing = fix and not exclude.matches(self.name, message)
        if fixing:
            hook_install.write(path, hook_install.merge(settings))
        yield Finding(self.name, message, resolved=fixing, fixable=True)


def bg_isolation_configured(settings: dict[str, object]) -> bool:
    """Whether ``worktree.bgIsolation: "none"`` is already set in a Claude settings dict."""
    worktree = settings.get('worktree')
    return isinstance(worktree, dict) and worktree.get('bgIsolation') == 'none'


class BgIsolationCheck:
    """Claude Code's own background-session isolation guard is turned off, since chimera's
    is the one that matters.

    A ``--bg`` session normally must call ``EnterWorktree`` before its first edit — Claude
    Code's own guard against a background agent silently editing the shared checkout
    (``worktree.bgIsolation``, added in Claude Code 2.1.143; ``"worktree"`` is its default).
    A chimera-launched agent never needs that: it always starts inside its own
    ``{goal}@{actor}`` worktree already, never the shared checkout (there often isn't even
    a working tree to edit — a chimera-managed project's ``repo/`` is a bare clone). Left at
    the default, the guard is pure friction — a session either wastes a turn calling
    ``EnterWorktree`` redundantly, or (worse) an agent already mid-task second-guesses
    itself into "isolating" out of a worktree it's already isolated in. ``--fix`` sets
    ``worktree.bgIsolation: "none"`` in the user's global ``~/.claude/settings.json`` — the
    same machine-wide Claude config ``claude-hooks`` above installs into, and for the same
    reason: there is no ``claude config set`` to shell out to, so a direct JSON merge is the
    only way in.
    """

    name = 'bg-isolation'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        path = hook_install.settings_path()
        settings = hook_install.read(path)
        if bg_isolation_configured(settings):
            return
        message = f'{path} missing worktree.bgIsolation: "none"'
        fixing = fix and not exclude.matches(self.name, message)
        if fixing:
            settings.setdefault('worktree', {})['bgIsolation'] = 'none'
            hook_install.write(path, settings)
        yield Finding(self.name, message, resolved=fixing, fixable=True)


# The lightweight model `commit_message` asks to summarise a workspace's staged changes,
# and the message it falls back to when claude can't be reached (so the fix still commits —
# leaving nothing uncommitted is the point, a perfect subject line is not).
_COMMIT_MODEL = 'haiku'
_COMMIT_PROMPT = (
    'Write a single-line git commit message (max 72 chars, imperative mood, no trailing '
    'period, no type prefix) summarising the staged changes in this Chimera workspace, '
    'which tracks config, knowledge notes, principles and processes. The staged diff is on '
    'stdin. Output only the message.'
)
_COMMIT_FALLBACK = 'Snapshot workspace changes'


def commit_message(diff: str) -> str:
    """A one-line commit message for a staged diff, from a lightweight model.

    Shells out to ``claude -p`` with a small model, feeding it the diff on stdin. Any failure
    (claude absent, non-zero exit, empty reply) falls back to a generic subject so ``--fix``
    still commits — its job is to leave nothing uncommitted, not to write the perfect message.
    """
    try:
        result = subprocess.run(
            ['claude', '-p', _COMMIT_PROMPT, '--model', _COMMIT_MODEL],
            input=diff,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return _COMMIT_FALLBACK
    return result.stdout.strip() or _COMMIT_FALLBACK


class WorkspaceCommitCheck:
    """The workspace's own git repo has no uncommitted or untracked content.

    Skipped when the workspace isn't a git repo. ``--fix`` stages everything and commits it
    with a message from a lightweight model (see ``commit_message``); the branch's before/after
    shas are logged for recovery. Runs last so it sweeps up the config/gitignore edits the
    earlier fixes made in the same pass.
    """

    name = 'workspace-clean'

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterator[Finding]:
        if not (workspace / '.git').exists():
            return  # not a git repo — nothing to commit
        git = Git(workspace)
        if not is_dirty(workspace):
            return
        dirty = f'{workspace} has uncommitted changes'
        if not fix or exclude.matches(self.name, dirty):
            yield Finding(self.name, dirty, resolved=False, fixable=True)
            return
        head = git('rev-parse', '--abbrev-ref', 'HEAD').strip()  # branch name, or 'HEAD' detached
        with git.ref_log(f'{self.name}: refs', head):
            git('add', '-A')
            message = commit_message(git('diff', '--cached'))
            git('commit', '-m', message)
        yield Finding(self.name, f'{workspace} committed: {message}', resolved=True, fixable=True)


CHECKS: tuple[Check, ...] = (
    WorkspaceConfigCheck(),
    GitignoreCheck(),
    WorkspaceDirsCheck(),
    CaptainCheck(),
    OccupancyWarningCheck(),
    ProjectConfigCheck(),
    RuntimeStateDirCheck(),
    ArchiveSchemaCheck(),
    HarnessContractCheck(),
    OpenSessionCheck(),
    StaleHumanWorktreeCheck(),
    InertBranchCheck(),
    LegacyWorktreeSeparatorCheck(),
    WorktreeBranchCheck(),
    OrphanedWorktreeCheck(),
    ChimeraUpToDateCheck(),
    WorkspaceEnvCheck(),
    ShellCompletionCheck(),
    FblogCheck(),
    ClaudeHooksCheck(),
    BgIsolationCheck(),
    WorkspaceCommitCheck(),
)
