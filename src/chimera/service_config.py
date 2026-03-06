from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field


class TmuxServiceConfig(BaseModel):
    type: Literal["tmux"]
    name: str
    use: str
    ports: dict[str, int] = {}
    session: str
    command: str


class DockerServiceConfig(BaseModel):
    type: Literal["docker"]
    name: str
    use: str
    ports: dict[str, int] = {}
    image: str
    command: str | None = None


class ProcessServiceConfig(BaseModel):
    type: Literal["process"]
    name: str
    use: str
    ports: dict[str, int] = {}
    command: str


ServiceConfig = Annotated[
    TmuxServiceConfig | DockerServiceConfig | ProcessServiceConfig,
    Field(discriminator="type"),
]


class ServicesConfig(BaseModel):
    services: list[ServiceConfig]


def load_services_config(path: Path) -> ServicesConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    return ServicesConfig.model_validate(data)
