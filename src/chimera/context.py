from dataclasses import dataclass
from pathlib import Path

from chimera.config import (
    NotInProjectError,
    ProjectConfig,
    find_project,
    find_workspace,
    load_config,
)


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


def resolve_project(cwd: Path, name: str | None = None) -> Project:
    """The project to act on: named under the workspace, else inferred from cwd."""
    if name is None:
        return Project(*find_project(cwd))
    directory = find_workspace(cwd) / name
    config = load_config(directory)
    if not isinstance(config, ProjectConfig):
        raise NotInProjectError(directory)
    return Project(directory, config)
