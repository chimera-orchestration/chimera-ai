import shutil
from pathlib import Path

import typer
from giterator import Git

_TEMPLATE = Path(__file__).parent.parent / 'templates' / 'workspace'


def init(path: Path) -> None:
    """Initialize a new workspace at path."""
    if path.exists():
        typer.echo(f'Error: {path} already exists', err=True)
        raise typer.Exit(1)
    shutil.copytree(_TEMPLATE, path)
    Git(path).init()
    typer.echo(f'Initialized workspace at {path}')
