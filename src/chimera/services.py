from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class ServiceBase(BaseModel):
    name: str
    use: str
    ports: dict[str, int] = {}
    started_at: datetime


class TmuxService(ServiceBase):
    type: Literal["tmux"]
    session: str


class DockerService(ServiceBase):
    type: Literal["docker"]
    container_id: str
    container_name: str


class ProcessService(ServiceBase):
    type: Literal["process"]
    pid: int
    cmd: str


Service = Annotated[
    TmuxService | DockerService | ProcessService,
    Field(discriminator="type"),
]
