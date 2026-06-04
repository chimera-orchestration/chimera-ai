from pathlib import Path
from typing import Annotated

import typer

from chimera.commands.init import init as _init
from chimera.commands.track import track as _track

app = typer.Typer()


@app.callback()
def callback() -> None:
    """Chimera — AI agent orchestration."""


@app.command()
def init(path: Annotated[Path, typer.Argument()]) -> None:
    typer.echo(f'Initialized workspace at {_init(path)}')


@app.command()
def track(path: Annotated[Path, typer.Argument()]) -> None:
    typer.echo(f'Tracking {path} at {_track(Path.cwd(), path)}')


if __name__ == '__main__':  # pragma: no cover
    app()
