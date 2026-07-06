from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, Field, TypeAdapter


class AgentConfig(BaseModel):
    """A level of the agent cascade: which harness runs sessions, on which model.

    Either field may be unset — resolution (``chimera.agents.registry.resolve_spec``)
    takes each field from the nearest level that sets it.
    """

    harness: str | None = None
    model: str | None = None


class CaptainConfig(AgentConfig):
    """The workspace's captain: its persona name, plus harness/model overrides.

    The captain is the workspace-level agent chatted with to direct all work (see
    AGENTS.md core concepts); ``name`` is what the workspace calls its own instance
    (lycia's captain is *pegasus*) and doubles as the chat session name.
    """

    name: str = 'captain'


def _name_shorthand(value: object) -> object:
    """Let config say ``captain: pegasus`` as shorthand for ``captain: {name: pegasus}``."""
    return {'name': value} if isinstance(value, str) else value


class WorkspaceConfig(BaseModel):
    kind: Literal['workspace']
    agent: AgentConfig = AgentConfig()
    captain: Annotated[CaptainConfig, BeforeValidator(_name_shorthand)] = CaptainConfig()


class ProjectConfig(BaseModel):
    kind: Literal['project']
    repo: Path
    agent: AgentConfig = AgentConfig()


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


def workspace_config(workspace: Path) -> WorkspaceConfig:
    """The parsed config of a resolved workspace root."""
    config = load_config(workspace)
    if not isinstance(config, WorkspaceConfig):
        raise NotInWorkspaceError(workspace)
    return config


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
