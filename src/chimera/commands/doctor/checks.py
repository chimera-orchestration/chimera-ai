import os
from collections.abc import Iterator
from pathlib import Path

from giterator import Git
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
    is_dirty,
    is_merged,
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
                False,
                False,
            )
            return
        kind = raw.get('kind') if raw else None
        if kind == 'workspace':
            return
        if kind is not None:
            yield Finding(
                self.name,
                f'{workspace}/config.yaml has kind: {kind} at the workspace root',
                False,
                False,
            )
            return
        if fix:
            write_config(workspace, {'kind': 'workspace', **(raw or {})})
        missing = 'missing' if raw is None else 'missing kind: workspace'
        yield Finding(self.name, f'{workspace}/config.yaml {missing}', fix, True)


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
            yield Finding(self.name, f'{gitignore} missing {entry!r}', fix, True)


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
                yield Finding(self.name, f'{project}/config.yaml {problem}', fix, True)
            elif kind is not None:
                yield Finding(
                    self.name, f'{project}/config.yaml has unexpected kind: {kind}', False, False
                )
            else:
                yield Finding(
                    self.name, f'{project}/config.yaml has no kind and no repo', False, False
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
                        self.name, f'{worktree} has {reason} — left in place', False, False
                    )
                    continue
                if fix:
                    git('worktree', 'remove', str(worktree))
                yield Finding(self.name, f'stale human worktree {worktree}', fix, True)


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
                    self.name, f'legacy worktree {worktree.name} → {canonical.name}', fix, True
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
                self.name, f'{worktree} is on {actual}, but branch {expected} is gone', False, False
            )
            return
        if is_dirty(worktree):
            yield Finding(
                self.name,
                f'{worktree} is on {actual}, expected {expected} — uncommitted changes, left in place',
                False,
                False,
            )
            return
        if fix:
            before = {actual: wt.rev_parse('HEAD', short=False)}
            wt('checkout', expected)
            logger.bind(
                worktree=str(worktree),
                git={'before': before, 'after': {expected: wt.rev_parse('HEAD', short=False)}},
            ).info(f'{self.name}: refs')
        yield Finding(self.name, f'{worktree} is on {actual}, expected {expected}', fix, True)


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
                    yield Finding(self.name, f'stale worktree registration for {path}', fix, True)
            worktrees_dir = project / 'worktrees'
            if worktrees_dir.is_dir():
                for child in sorted(worktrees_dir.iterdir()):
                    if child.is_dir() and child.resolve() not in registered:
                        yield Finding(
                            self.name, f'{child} is not a registered worktree', False, False
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
                self.name, f'$CHIMERA_WORKSPACE is not set — {hint}\n    {export}', False, False
            )
        elif Path(env).expanduser().resolve() != workspace.resolve():
            yield Finding(
                self.name,
                f'$CHIMERA_WORKSPACE is {env}, not this workspace — {hint}\n    {export}',
                False,
                False,
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
            False,
            False,
        )


CHECKS: tuple[Check, ...] = (
    WorkspaceConfigCheck(),
    GitignoreCheck(),
    ProjectConfigCheck(),
    StaleHumanWorktreeCheck(),
    LegacyWorktreeSeparatorCheck(),
    WorktreeBranchCheck(),
    OrphanedWorktreeCheck(),
    WorkspaceEnvCheck(),
    ShellCompletionCheck(),
)
