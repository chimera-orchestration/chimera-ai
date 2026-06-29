import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

from giterator import Git, GitError
from loguru import logger

from chimera.commands.doctor.core import (
    Check,
    Finding,
    iter_project_dirs,
    project_repo,
    read_raw,
    write_config,
)
from chimera.commands.init import TEMPLATE
from chimera.worktrees import (
    HUMAN,
    SEP,
    base_ref,
    branch,
    default_branch,
    fetch_origin,
    is_dirty,
    is_merged,
    ref_shas,
    registered_worktrees,
    worktree_path,
)


class WorkspaceConfigCheck:
    """The workspace root carries config.yaml with kind: workspace."""

    name = 'workspace-config'

    def run(self, workspace: Path, fix: bool) -> Iterator[Finding]:
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
        if fix:
            write_config(workspace, {'kind': 'workspace', **(raw or {})})
        missing = 'missing' if raw is None else 'missing kind: workspace'
        yield Finding(self.name, f'{workspace}/config.yaml {missing}', resolved=fix, fixable=True)


def _gitignore_entries(path: Path) -> list[str]:
    """Non-blank, stripped lines of a .gitignore, [] if it's absent."""
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


class GitignoreCheck:
    """The workspace .gitignore carries every entry the current template ships."""

    name = 'gitignore'

    def run(self, workspace: Path, fix: bool) -> Iterator[Finding]:
        gitignore = workspace / '.gitignore'
        have = _gitignore_entries(gitignore)
        missing = [e for e in _gitignore_entries(TEMPLATE / '.gitignore') if e not in have]
        if missing and fix:
            text = gitignore.read_text() if gitignore.exists() else ''
            if text and not text.endswith('\n'):
                text += '\n'
            gitignore.write_text(text + ''.join(f'{e}\n' for e in missing))
        for entry in missing:
            yield Finding(self.name, f'{gitignore} missing {entry!r}', resolved=fix, fixable=True)


class ProjectConfigCheck:
    """Each project's config.yaml carries kind: project."""

    name = 'project-config'

    def run(self, workspace: Path, fix: bool) -> Iterator[Finding]:
        for project in iter_project_dirs(workspace):
            raw = read_raw(project) or {}
            kind = raw.get('kind')
            if kind == 'project':
                continue
            if 'repo' in raw:
                # repo: is the authoritative project signal — set the right kind,
                # dropping any wrong one (e.g. a stray kind: workspace).
                if fix:
                    write_config(
                        project,
                        {'kind': 'project', **{k: v for k, v in raw.items() if k != 'kind'}},
                    )
                problem = (
                    'missing kind: project'
                    if kind is None
                    else f'has kind: {kind} but repo: marks it a project'
                )
                yield Finding(
                    self.name, f'{project}/config.yaml {problem}', resolved=fix, fixable=True
                )
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


class StaleHumanWorktreeCheck:
    """Legacy {goal}-human worktrees are gone; the human branch survives bare."""

    name = 'human-worktrees'

    def run(self, workspace: Path, fix: bool) -> Iterator[Finding]:
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
                if fix:
                    git('worktree', 'remove', str(worktree))
                yield Finding(
                    self.name, f'stale human worktree {worktree}', resolved=fix, fixable=True
                )


class LegacyWorktreeSeparatorCheck:
    """Agent worktree dirs use <goal>@<actor>, migrating the legacy <goal>-<actor>."""

    name = 'worktree-separator'

    def run(self, workspace: Path, fix: bool) -> Iterator[Finding]:
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
                if fix:
                    git('worktree', 'move', str(worktree), str(canonical))
                yield Finding(
                    self.name,
                    f'legacy worktree {worktree.name} → {canonical.name}',
                    resolved=fix,
                    fixable=True,
                )


def _canonical_worktree(worktree: Path) -> Path | None:
    """Where worktree should live given its <goal>/<actor> branch; None if it has none.

    A missing dir (a stale registration) is the orphaned-worktrees check's concern.
    Human worktrees return None too — the human-worktrees check removes rather than
    renames them.
    """
    if not worktree.is_dir():
        return None
    branch = Git(worktree)('rev-parse', '--abbrev-ref', 'HEAD').strip()
    if '/' not in branch:  # detached HEAD or a plain branch — not a managed worktree
        return None
    goal, actor = branch.rsplit('/', 1)
    if actor == HUMAN:
        return None
    return worktree_path(worktree.parent, goal, actor)


class WorktreeBranchCheck:
    """Each agent worktree is on the branch its <goal>@<actor> name implies.

    The inverse of the separator check: that one trusts the branch and fixes the dir
    name; this one trusts the dir name and fixes the branch. Catches a git GUI flipping
    a worktree onto the wrong branch (or detaching its HEAD) — the dir still says which
    branch belongs here. ``--fix`` checks the right branch back out, but only when the
    worktree is clean (a dirty switch could lose uncommitted work); the before/after
    HEAD shas are logged first so the move can be undone (see ``agent-docs/logging.md``).
    """

    name = 'worktree-branch'

    def run(self, workspace: Path, fix: bool) -> Iterator[Finding]:
        for project in iter_project_dirs(workspace):
            repo = project_repo(project)
            worktrees_dir = project / 'worktrees'
            if repo is None or not repo.is_dir() or not worktrees_dir.is_dir():
                continue
            git = Git(repo)
            branches = set(git.branches())
            root = worktrees_dir.resolve()
            for worktree in sorted(p for p in registered_worktrees(git) if p.parent == root):
                yield from self._check(worktree, branches, fix)

    def _check(self, worktree: Path, branches: set[str], fix: bool) -> Iterator[Finding]:
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
            yield Finding(
                self.name,
                f'{worktree} is on {actual}, but branch {expected} is gone',
                resolved=False,
                fixable=False,
            )
            return
        if is_dirty(worktree):
            yield Finding(
                self.name,
                f'{worktree} is on {actual}, expected {expected} — uncommitted changes, left in place',
                resolved=False,
                fixable=False,
            )
            return
        if fix:
            before = {actual: wt.rev_parse('HEAD', short=False)}
            wt('checkout', expected)
            logger.bind(
                worktree=str(worktree),
                git={'before': before, 'after': {expected: wt.rev_parse('HEAD', short=False)}},
            ).info(f'{self.name}: refs')
        yield Finding(
            self.name, f'{worktree} is on {actual}, expected {expected}', resolved=fix, fixable=True
        )


class OrphanedWorktreeCheck:
    """Git's worktree registrations and the worktrees/ dir agree with each other."""

    name = 'orphaned-worktrees'

    def run(self, workspace: Path, fix: bool) -> Iterator[Finding]:
        for project in iter_project_dirs(workspace):
            repo = project_repo(project)
            if repo is None or not repo.is_dir():
                continue
            git = Git(repo)
            registered = registered_worktrees(git)
            stale = sorted(path for path in registered if not path.exists())
            if stale:
                if fix:
                    git('worktree', 'prune')
                for path in stale:
                    yield Finding(
                        self.name,
                        f'stale worktree registration for {path}',
                        resolved=fix,
                        fixable=True,
                    )
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


class ChimeraUpToDateCheck:
    """Chimera's own checkout is current with origin, and any deploy branch tracks main.

    Skipped entirely when chimera isn't running from a git checkout. ``git fetch`` always
    runs, even on a plain check — it's read-only. ``--fix`` fast-forwards the default branch
    to ``origin/<default>`` once ancestry confirms it's a true fast-forward — local already
    containing everything origin has needs no action, and a divergent history needs a human,
    so both are left untouched (reported, not fixed, in the divergent case). Only once the
    default branch is confirmed current does a ``deploy`` branch, if one exists, get checked
    — and if needed, repointed — against it.
    """

    name = 'chimera-up-to-date'

    def run(self, workspace: Path, fix: bool) -> Iterator[Finding]:
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
        current = yield from self._sync_default(git, repo, default, local_sha, remote_sha, fix)
        if current is None:
            return  # default branch needs a human before deploy can be trusted against it
        yield from self._sync_deploy(git, repo, default, current, fix)

    def _sync_default(
        self, git: Git, repo: Path, default: str, local_sha: str, remote_sha: str, fix: bool
    ) -> Iterator[Finding]:
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
        if not fix:
            yield Finding(
                self.name, f'{repo} {default} is behind {remote}', resolved=False, fixable=True
            )
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
        self, git: Git, repo: Path, default: str, current: str, fix: bool
    ) -> Iterator[Finding]:
        deploy_sha = git.rev_parse('deploy', short=False) if 'deploy' in git.branches() else None
        if deploy_sha is None or deploy_sha == current:
            return
        if not fix:
            yield Finding(
                self.name,
                f'{repo} deploy does not point at {default}',
                resolved=False,
                fixable=True,
            )
            return
        new_sha = _repoint(git, 'deploy', default)
        if new_sha is None:
            yield Finding(
                self.name,
                f'{repo} deploy does not point at {default} — '
                'could not repoint, branch checked out elsewhere',
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

    def run(self, workspace: Path, fix: bool) -> Iterator[Finding]:
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

    def run(self, workspace: Path, fix: bool) -> Iterator[Finding]:
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

    def run(self, workspace: Path, fix: bool) -> Iterator[Finding]:
        if not (workspace / '.git').exists():
            return  # not a git repo — nothing to commit
        git = Git(workspace)
        if not is_dirty(workspace):
            return
        if not fix:
            yield Finding(
                self.name, f'{workspace} has uncommitted changes', resolved=False, fixable=True
            )
            return
        head = git('rev-parse', '--abbrev-ref', 'HEAD').strip()  # branch name, or 'HEAD' detached
        before = ref_shas(git, head)
        git('add', '-A')
        message = commit_message(git('diff', '--cached'))
        git('commit', '-m', message)
        logger.bind(git={'before': before, 'after': ref_shas(git, head)}).info(f'{self.name}: refs')
        yield Finding(self.name, f'{workspace} committed: {message}', resolved=True, fixable=True)


CHECKS: tuple[Check, ...] = (
    WorkspaceConfigCheck(),
    GitignoreCheck(),
    ProjectConfigCheck(),
    StaleHumanWorktreeCheck(),
    LegacyWorktreeSeparatorCheck(),
    WorktreeBranchCheck(),
    OrphanedWorktreeCheck(),
    ChimeraUpToDateCheck(),
    WorkspaceEnvCheck(),
    ShellCompletionCheck(),
    WorkspaceCommitCheck(),
)
