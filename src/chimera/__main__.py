from pathlib import Path
from typing import Annotated

import typer
import yaml

from chimera.commands.goal.cleanup import cleanup as _goal_cleanup
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


def _project() -> tuple[Path, Path]:
    project = Path.cwd()
    repo = Path(yaml.safe_load((project / 'config.yaml').read_text())['repo'])
    return repo, project / 'worktrees'


@goal_app.command()
def new(goal: Annotated[str, typer.Argument()]) -> None:
    repo, worktrees = _project()
    for worktree in _goal_new(repo, worktrees, goal):
        typer.echo(f'Created {worktree}')


@goal_app.command()
def cleanup(
    goal: Annotated[str, typer.Argument()],
    force: Annotated[bool, typer.Option('--force')] = False,
) -> None:
    repo, worktrees = _project()
    for worktree in _goal_cleanup(repo, worktrees, goal, force):
        typer.echo(f'Removed {worktree}')


if __name__ == '__main__':  # pragma: no cover
    app()
