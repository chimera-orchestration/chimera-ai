from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field


class ServiceBase(BaseModel):
    name: str
    use: str
    ports: dict[str, int] = {}


class TmuxBase(ServiceBase):
    session: str


# --- Config models (desired state, from services-config.yaml) ---


class TmuxServiceConfig(TmuxBase):
    type: Literal["tmux"]
    command: str


class DockerServiceConfig(ServiceBase):
    type: Literal["docker"]
    image: str
    command: str | None = None


class ProcessServiceConfig(ServiceBase):
    type: Literal["process"]
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


# --- Runtime models (actual state, from services-running.jsonl) ---


class TmuxService(TmuxBase):
    type: Literal["tmux"]
    started_at: datetime


class DockerService(ServiceBase):
    type: Literal["docker"]
    started_at: datetime
    container_id: str
    container_name: str


class ProcessService(ServiceBase):
    type: Literal["process"]
    started_at: datetime
    pid: int
    cmd: str


Service = Annotated[
    TmuxService | DockerService | ProcessService,
    Field(discriminator="type"),
]
