from collections.abc import Iterator
from pathlib import Path

from giterator import Git

from chimera.commands.doctor.core import (
    Check,
    Finding,
    iter_project_dirs,
    project_repo,
    read_raw,
    write_config,
)
from chimera.worktrees import is_dirty, is_merged, registered_worktrees


class WorkspaceConfigCheck:
    """The workspace root carries config.yaml with kind: workspace."""

    name = 'workspace-config'

    def run(self, workspace: Path, fix: bool) -> Iterator[Finding]:
        raw = read_raw(workspace)
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


class ProjectConfigCheck:
    """Each project's config.yaml carries kind: project."""

    name = 'project-config'

    def run(self, workspace: Path, fix: bool) -> Iterator[Finding]:
        for project in iter_project_dirs(workspace):
            raw = read_raw(project) or {}
            kind = raw.get('kind')
            if kind == 'project':
                continue
            if kind is not None:
                yield Finding(
                    self.name, f'{project}/config.yaml has unexpected kind: {kind}', False, False
                )
            elif 'repo' in raw:
                if fix:
                    write_config(project, {'kind': 'project', **raw})
                yield Finding(self.name, f'{project}/config.yaml missing kind: project', fix, True)
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
            for worktree in sorted(worktrees_dir.glob('*-human')):
                if not worktree.is_dir() or worktree.resolve() not in registered:
                    continue  # a leftover dir, not a real worktree — orphan check covers it
                branch = f'{worktree.name.removesuffix("-human")}/human'
                dirty = is_dirty(worktree)
                unmerged = branch in branches and not is_merged(git, branch)
                if dirty or unmerged:
                    reason = 'uncommitted changes' if dirty else 'unmerged commits'
                    yield Finding(
                        self.name, f'{worktree} has {reason} — left in place', False, False
                    )
                    continue
                if fix:
                    git('worktree', 'remove', str(worktree))
                yield Finding(self.name, f'stale human worktree {worktree}', fix, True)


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


CHECKS: tuple[Check, ...] = (
    WorkspaceConfigCheck(),
    ProjectConfigCheck(),
    StaleHumanWorktreeCheck(),
    OrphanedWorktreeCheck(),
)
