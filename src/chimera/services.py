from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class Service(BaseModel):
    name: str
    use: str
    ports: dict[str, int] = {}
    started_at: datetime


class TmuxService(Service):
    type: Literal["tmux"]
    session: str


class DockerService(Service):
    type: Literal["docker"]
    container_id: str
    container_name: str


class ProcessService(Service):
    type: Literal["process"]
    pid: int
    cmd: str


AnyService = Annotated[
    TmuxService | DockerService | ProcessService,
    Field(discriminator="type"),
]
