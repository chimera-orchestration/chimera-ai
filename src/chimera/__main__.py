from pathlib import Path
from typing import Annotated

import typer

from chimera.commands.agent import agent as _agent
from chimera.commands.goal.cleanup import cleanup as _goal_cleanup
from chimera.commands.goal.new import new as _goal_new
from chimera.commands.init import init as _init
from chimera.commands.project.forget import forget as _forget
from chimera.commands.project.track import track as _track
from chimera.config import find_project, find_workspace

app = typer.Typer()


@app.callback()
def callback() -> None:
    """Chimera — AI agent orchestration."""


@app.command()
def init(path: Annotated[Path, typer.Argument()]) -> None:
    typer.echo(f'Initialized workspace at {_init(path)}')


@app.command()
def agent(
    goal: Annotated[str, typer.Argument()],
    prompt: Annotated[str | None, typer.Argument()] = None,
) -> None:
    project, _config = find_project(Path.cwd())
    worktree = project / 'worktrees' / f'{goal}-agent'
    name = f'{project.name}-{goal}'
    _agent(worktree, name, prompt)
    typer.echo(f'Launched agent {name} in {worktree}')


goal_app = typer.Typer()
app.add_typer(goal_app, name='goal')


@goal_app.callback()
def goal() -> None:
    """Manage goals."""


def _project() -> tuple[Path, Path]:
    project, config = find_project(Path.cwd())
    return config.repo, project / 'worktrees'


@goal_app.command()
def new(
    goal: Annotated[str, typer.Argument()],
    branch: Annotated[
        str | None,
        typer.Option(
            '--branch', help='Start point for the branches (default: newest of main/origin/main)'
        ),
    ] = None,
) -> None:
    repo, worktrees = _project()
    typer.echo(f'Created {_goal_new(repo, worktrees, goal, branch)}')


@goal_app.command()
def cleanup(
    goal: Annotated[str, typer.Argument()],
    force: Annotated[bool, typer.Option('--force')] = False,
) -> None:
    repo, worktrees = _project()
    removed = _goal_cleanup(repo, worktrees, goal, force)
    for worktree in removed:
        typer.echo(f'Removed {worktree}')
    if not removed:
        typer.echo(f'Nothing to clean up for {goal}')


project_app = typer.Typer()
app.add_typer(project_app, name='project')


@project_app.callback()
def project() -> None:
    """Manage projects."""


@project_app.command()
def track(path: Annotated[Path, typer.Argument()]) -> None:
    workspace = find_workspace(Path.cwd())
    typer.echo(f'Tracking {path} at {_track(workspace, path)}')


@project_app.command()
def forget(
    name: Annotated[str, typer.Argument()],
    force: Annotated[bool, typer.Option('--force')] = False,
) -> None:
    removed = _forget(Path.cwd(), name, force)
    typer.echo(f'Forgot {removed}' if removed else f'No project named {name} to forget')


if __name__ == '__main__':  # pragma: no cover
    app()
