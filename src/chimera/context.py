import os
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path

from giterator import Git, GitError

from chimera.config import (
    NotInWorkspaceError,
    ProjectConfig,
    UserError,
    WorkspaceConfig,
    find_workspace,
    load_config,
)
from chimera.worktrees import goals


class CannotIdentifyProjectError(UserError):
    def __init__(self, cwd: Path) -> None:
        super().__init__(f'cannot identify a project from {cwd}; pass --project')


class UnknownProjectError(UserError):
    """A ``--project`` name that doesn't match any tracked project — with a typo hint."""

    def __init__(self, name: str, workspace: Path) -> None:
        available = [project.name for project in iter_projects(workspace)]
        hint = (
            f", did you mean '{match[0]}'?" if (match := get_close_matches(name, available)) else ''
        )
        super().__init__(f"no project '{name}'{hint} (available: {', '.join(available) or 'none'})")


class GoalRequiredError(UserError):
    def __init__(self, cwd: Path) -> None:
        super().__init__(f'cannot infer a goal from {cwd}; pass --goal')


@dataclass(frozen=True)
class Project:
    """A resolved project: its directory and parsed config."""

    dir: Path
    config: ProjectConfig

    @property
    def name(self) -> str:
        return self.dir.name

    @property
    def repo(self) -> Path:
        return self.config.repo

    @property
    def worktrees(self) -> Path:
        return self.dir / 'worktrees'


@dataclass(frozen=True)
class Scope:
    """The resolved axes a lister enumerates over.

    ``project``/``goal`` are ``None`` when they couldn't be pinned — listing then
    widens to all projects / all goals, rather than raising as the actions do.
    """

    workspace: Path
    project: Project | None
    goal: str | None


def resolve_workspace(cwd: Path) -> Path:
    """The workspace: ``$CHIMERA_WORKSPACE`` if set (the norm), else walk up from cwd."""
    if env := os.environ.get('CHIMERA_WORKSPACE'):
        workspace = Path(env).expanduser()
        if not isinstance(load_config(workspace), WorkspaceConfig):
            raise NotInWorkspaceError(workspace)
        return workspace
    return find_workspace(cwd)


def iter_projects(workspace: Path) -> list[Project]:
    """The tracked projects in the workspace, sorted by name."""
    return [
        Project(child, config)
        for child in sorted(workspace.iterdir())
        if child.is_dir() and isinstance(config := load_config(child), ProjectConfig)
    ]


def resolve_project(cwd: Path, explicit: str | None = None) -> Project:
    """The project to act on: named under the workspace, inferred from cwd, or matched by repo.

    With ``explicit`` (``-p``), the project of that name under the workspace. Otherwise
    walk up from cwd to a project dir; failing that — e.g. from a checkout outside the
    workspace — identify it by matching the git repo against each project's ``repo``.
    """
    if explicit is not None:
        workspace = resolve_workspace(cwd)
        if isinstance(config := load_config(workspace / explicit), ProjectConfig):
            return Project(workspace / explicit, config)
        raise UnknownProjectError(explicit, workspace)
    for directory in (cwd, *cwd.parents):
        config = load_config(directory)
        if isinstance(config, ProjectConfig):
            return Project(directory, config)
        if isinstance(config, WorkspaceConfig):
            break  # reached the workspace root without crossing a project
    return _match_repo(cwd, resolve_workspace(cwd))


def resolve_goal(cwd: Path, project: Project, explicit: str | None = None) -> str:
    """The goal to act on: ``explicit`` (``-g``), else the current ``<goal>/<actor>`` branch.

    The branch is trusted only when it names a goal that actually exists, so a review or
    feature branch is never mistaken for one. Raises when neither source applies.
    """
    if explicit is not None:
        return explicit
    token = _branch_token(cwd)
    if token is not None and token[0] in goals(project.worktrees):
        return token[0]
    raise GoalRequiredError(cwd)


def resolve_scope(
    cwd: Path, *, project: str | None = None, goal: str | None = None, infer: bool = True
) -> Scope:
    """The scope to list within.

    With ``infer`` (the default, for ``goal ls``/``agent ls``) the narrowest axis is pinned
    from cwd/flags, and *inference* failure widens rather than raises —
    ``CannotIdentifyProjectError`` → all projects, ``GoalRequiredError`` → all goals.
    Without it (the ``ch ls`` dashboard) only an explicit ``--project``/``--goal`` narrows;
    cwd is never read, so the view stays workspace-wide wherever you stand.

    A bad explicit ``--project`` always raises ``UnknownProjectError`` (naming a ghost is an
    error, not a reason to widen), as does genuinely not being in a workspace.
    """
    workspace = resolve_workspace(cwd)
    if not infer:
        project_ = resolve_project(cwd, project) if project is not None else None
        return Scope(workspace, project_, goal)
    try:
        resolved: Project | None = resolve_project(cwd, project)
    except CannotIdentifyProjectError:
        resolved = None
    pinned_goal: str | None = None
    if resolved is not None:
        try:
            pinned_goal = resolve_goal(cwd, resolved, goal)
        except GoalRequiredError:
            pinned_goal = None
    return Scope(workspace, resolved, pinned_goal)


def _match_repo(cwd: Path, workspace: Path) -> Project:
    repo = _repo_root(cwd)
    if repo is not None:
        for project in iter_projects(workspace):
            if project.repo.resolve() == repo:
                return project
    raise CannotIdentifyProjectError(cwd)


def _repo_root(cwd: Path) -> Path | None:
    """The working root of the git repo at cwd (the main repo, even from a linked worktree)."""
    try:
        common = Git(cwd)('rev-parse', '--git-common-dir').strip()
    except GitError:
        return None
    path = Path(common)
    if not path.is_absolute():
        path = cwd / path
    path = path.resolve()
    return path.parent if path.name == '.git' else path


def _branch_token(cwd: Path) -> tuple[str, str] | None:
    """``(goal, actor)`` parsed from the current ``<goal>/<actor>`` branch, else None."""
    try:
        branch = Git(cwd)('rev-parse', '--abbrev-ref', 'HEAD').strip()
    except GitError:
        return None
    if '/' not in branch:  # detached HEAD ('HEAD') or a plain branch name
        return None
    goal, actor = branch.rsplit('/', 1)
    return goal, actor
