from pathlib import Path
from typing import Annotated

import typer

from chimera.commands.agent import agent as _agent
from chimera.commands.agent import all_sessions
from chimera.commands.doctor import CHECKS, Finding, find_workspace_root
from chimera.commands.doctor import doctor as _doctor
from chimera.commands.goal.start import start as _goal_start
from chimera.commands.init import init as _init
from chimera.commands.project.add import add as _project_add
from chimera.commands.project.ls import projects as _projects
from chimera.commands.project.rm import remove as _project_remove
from chimera.commands.worktree.add import add as _worktree_add
from chimera.commands.worktree.rm import remove as _worktree_remove
from chimera.context import resolve_goal, resolve_project, resolve_workspace
from chimera.worktrees import ACTORS, AGENT, goals, worktree_dirs, worktree_path

app = typer.Typer()

# Reusable option types — declared once, shared across commands (callables never see them).
ProjectOpt = Annotated[
    str | None, typer.Option('--project', '-p', help='Project name (default: inferred from cwd)')
]
GoalOpt = Annotated[
    str | None, typer.Option('--goal', '-g', help='Goal (default: inferred from cwd/branch)')
]
ActorOpt = Annotated[str, typer.Option('--actor', '-a', help='Actor (default: agent)')]
FromOpt = Annotated[
    str | None, typer.Option('--from', help='Start ref (default: newest of main/origin/main)')
]
PromptOpt = Annotated[
    str | None, typer.Option('--prompt', help='Prompt; its presence runs the agent in background')
]
ForceOpt = Annotated[bool, typer.Option('--force')]


@app.callback()
def callback() -> None:
    """Chimera — AI agent orchestration."""


@app.command()
def init(path: Annotated[Path, typer.Argument()]) -> None:
    typer.echo(f'Initialized workspace at {_init(path)}')


@app.command()
def doctor(
    path: Annotated[Path | None, typer.Argument()] = None,
    fix: Annotated[
        bool, typer.Option('--fix', help='Apply the fixes instead of only reporting')
    ] = False,
    verbose: Annotated[
        bool, typer.Option('--verbose', '-v', help='Show every check, including the ones that pass')
    ] = False,
) -> None:
    target = (path or Path.cwd()).resolve()
    root = find_workspace_root(target)
    if root != target:
        typer.echo(f'note: resolved workspace root: {root}')
    findings = _doctor(root, fix)
    by_check: dict[str, list[Finding]] = {}
    for finding in findings:
        by_check.setdefault(finding.check, []).append(finding)
    for check in CHECKS:
        reported = by_check.get(check.name)
        if reported:
            for finding in reported:
                typer.echo(f'[{finding.check}] ({_tag(finding)}) {finding.message}')
        elif verbose:
            typer.echo(f'[{check.name}] (ok)')
    if not findings:
        typer.echo('All checks passed!')
    if any(not finding.resolved for finding in findings):
        raise typer.Exit(1)


def _tag(finding: Finding) -> str:
    if finding.resolved:
        return 'fixed'
    return 'would fix — run with --fix' if finding.fixable else 'needs attention'


project_app = typer.Typer(help='Manage projects.')
app.add_typer(project_app, name='project')


@project_app.command('add')
def project_add(
    source: Annotated[str, typer.Argument(help='Git URL to clone, or local path to track')],
) -> None:
    typer.echo(f'Added {_project_add(resolve_workspace(Path.cwd()), source)}')


@project_app.command('rm')
def project_rm(name: Annotated[str, typer.Argument()], force: ForceOpt = False) -> None:
    removed = _project_remove(resolve_workspace(Path.cwd()), name, force)
    typer.echo(f'Removed {removed}' if removed else f'No project named {name} to remove')


@project_app.command('ls')
def project_ls() -> None:
    for name in _projects(resolve_workspace(Path.cwd())):
        typer.echo(name)


worktree_app = typer.Typer(help="Manage a goal's worktrees and branches.")
app.add_typer(worktree_app, name='worktree')


@worktree_app.command('add')
def worktree_add(
    goal: Annotated[str, typer.Argument()],
    actors: Annotated[
        list[str] | None, typer.Argument(help='Actors (default: human, agent)')
    ] = None,
    frm: FromOpt = None,
    project: ProjectOpt = None,
) -> None:
    p = resolve_project(Path.cwd(), project)
    for created in _worktree_add(
        p.repo, p.worktrees, goal, tuple(actors) if actors else ACTORS, frm
    ):
        typer.echo(f'Created {created}')


@worktree_app.command('rm')
def worktree_rm(
    goal: Annotated[str, typer.Argument()], force: ForceOpt = False, project: ProjectOpt = None
) -> None:
    _report_removed(_worktree_remove(*_project_args(project), goal, force), goal)


@worktree_app.command('ls')
def worktree_ls(project: ProjectOpt = None) -> None:
    for worktree in worktree_dirs(resolve_project(Path.cwd(), project).worktrees):
        typer.echo(worktree)


goal_app = typer.Typer(help='Work on goals.')
app.add_typer(goal_app, name='goal')


@goal_app.command('start')
def goal_start(
    goal: Annotated[str, typer.Argument()],
    prompt: PromptOpt = None,
    frm: FromOpt = None,
    project: ProjectOpt = None,
) -> None:
    p = resolve_project(Path.cwd(), project)
    worktree = _goal_start(p.repo, p.worktrees, goal, f'{p.name}-{goal}-{AGENT}', prompt, frm)
    typer.echo(f'Started {goal} in {worktree}')


@goal_app.command('finish')
def goal_finish(
    goal: Annotated[str, typer.Argument()], force: ForceOpt = False, project: ProjectOpt = None
) -> None:
    _report_removed(_worktree_remove(*_project_args(project), goal, force), goal)


@goal_app.command('ls')
def goal_ls(project: ProjectOpt = None) -> None:
    for goal in sorted(goals(resolve_project(Path.cwd(), project).worktrees)):
        typer.echo(goal)


agent_app = typer.Typer(help='Launch and list agents.')
app.add_typer(agent_app, name='agent')


@agent_app.command('start')
def agent_start(
    goal: GoalOpt = None,
    actor: ActorOpt = AGENT,
    prompt: PromptOpt = None,
    project: ProjectOpt = None,
) -> None:
    p = resolve_project(Path.cwd(), project)
    g = resolve_goal(Path.cwd(), p, goal)
    worktree = worktree_path(p.worktrees, g, actor)
    _agent(worktree, f'{p.name}-{g}-{actor}', prompt)
    typer.echo(f'Launched agent in {worktree}')


@agent_app.command('ls')
def agent_ls() -> None:
    sessions = all_sessions()
    for session in sessions:
        typer.echo(f'{session["sessionId"]} {session["status"]}')
    if not sessions:
        typer.echo('No agents running')


def _project_args(project: str | None) -> tuple[Path, Path]:
    p = resolve_project(Path.cwd(), project)
    return p.repo, p.worktrees


def _report_removed(removed: list[Path], goal: str) -> None:
    for worktree in removed:
        typer.echo(f'Removed {worktree}')
    if not removed:
        typer.echo(f'Nothing to remove for {goal}')


if __name__ == '__main__':  # pragma: no cover
    app()
