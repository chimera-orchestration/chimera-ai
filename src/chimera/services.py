from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class TmuxService(BaseModel):
    type: Literal["tmux"]
    name: str
    use: str
    ports: dict[str, int]
    started_at: datetime
    session: str


class DockerService(BaseModel):
    type: Literal["docker"]
    name: str
    use: str
    ports: dict[str, int]
    started_at: datetime
    container_id: str
    container_name: str


class ProcessService(BaseModel):
    type: Literal["process"]
    name: str
    use: str
    ports: dict[str, int]
    started_at: datetime
    pid: int
    cmd: str


Service = Annotated[
    TmuxService | DockerService | ProcessService,
    Field(discriminator="type"),
]
