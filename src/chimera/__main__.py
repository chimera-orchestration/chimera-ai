from pathlib import Path
from typing import Annotated

import typer
import yaml

from chimera.commands.goal.new import new as _goal_new
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


goal_app = typer.Typer()
app.add_typer(goal_app, name='goal')


@goal_app.callback()
def goal() -> None:
    """Manage goals."""


@goal_app.command()
def new(goal: Annotated[str, typer.Argument()]) -> None:
    project = Path.cwd()
    repo = Path(yaml.safe_load((project / 'config.yaml').read_text())['repo'])
    goal_dir = _goal_new(repo, project / 'worktrees', goal)
    typer.echo(f'Created goal {goal} at {goal_dir}')


if __name__ == '__main__':  # pragma: no cover
    app()
