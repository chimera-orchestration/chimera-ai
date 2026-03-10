from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field


class ServiceConfig(BaseModel):
    name: str
    use: str
    ports: dict[str, int] = {}


class TmuxServiceConfig(ServiceConfig):
    type: Literal["tmux"]
    session: str
    command: str


class DockerServiceConfig(ServiceConfig):
    type: Literal["docker"]
    image: str
    command: str | None = None


class ProcessServiceConfig(ServiceConfig):
    type: Literal["process"]
    command: str


AnyServiceConfig = Annotated[
    TmuxServiceConfig | DockerServiceConfig | ProcessServiceConfig,
    Field(discriminator="type"),
]


class ServicesConfig(BaseModel):
    services: list[AnyServiceConfig]


def load_services_config(path: Path) -> ServicesConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    return ServicesConfig.model_validate(data)
