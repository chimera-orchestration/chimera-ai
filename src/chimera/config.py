from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, TypeAdapter


class WorkspaceConfig(BaseModel):
    kind: Literal['workspace']


class ProjectConfig(BaseModel):
    kind: Literal['project']
    repo: Path


AnyConfig = Annotated[WorkspaceConfig | ProjectConfig, Field(discriminator='kind')]

_ADAPTER: TypeAdapter[AnyConfig] = TypeAdapter(AnyConfig)


class UserError(Exception):
    """An error meant for the user: shown as a one-line message, never a traceback.

    Raised when the fault is in what was asked for (a bad name, the wrong directory),
    not a bug. The CLI chokepoint (``LoggingCommand``) catches the whole family and
    prints ``str(error)`` to stderr with a non-zero exit, so no two error sites have to
    agree on how to present themselves.
    """


class NotInWorkspaceError(UserError):
    def __init__(self, start: Path) -> None:
        super().__init__(f'{start} is not inside a Chimera workspace')


class NotInProjectError(UserError):
    def __init__(self, start: Path) -> None:
        super().__init__(f'{start} is not inside a Chimera project')


def load_config(directory: Path) -> AnyConfig | None:
    """Parse the config.yaml in directory into its model, or None if there is none."""
    path = directory / 'config.yaml'
    if not path.exists():
        return None
    return _ADAPTER.validate_python(yaml.safe_load(path.read_text()))


def find_workspace(start: Path) -> Path:
    """Walk up from start to the nearest workspace root; raise if there is none."""
    for directory in (start, *start.parents):
        if isinstance(load_config(directory), WorkspaceConfig):
            return directory
    raise NotInWorkspaceError(start)


def find_project(start: Path) -> tuple[Path, ProjectConfig]:
    """Walk up from start to the nearest project dir and its config; raise if there is none."""
    for directory in (start, *start.parents):
        config = load_config(directory)
        if isinstance(config, ProjectConfig):
            return directory, config
    raise NotInProjectError(start)
