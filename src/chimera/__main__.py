from pathlib import Path
from typing import Annotated

import typer

from chimera.commands.init import init as _init

app = typer.Typer()


@app.callback()
def callback() -> None:
    """Chimera — AI agent orchestration."""


@app.command()
def init(path: Annotated[Path, typer.Argument()]) -> None:
    typer.echo(f'Initialized workspace at {_init(path)}')


if __name__ == '__main__':  # pragma: no cover
    app()
