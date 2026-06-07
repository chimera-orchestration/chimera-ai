import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from chimera.commands.agent import agent as _agent
from chimera.commands.agent import agents
from chimera.commands.doctor import CHECKS, Finding, resolve_root
from chimera.commands.doctor import doctor as _doctor
from chimera.commands.goal.start import start as _goal_start
from chimera.commands.init import init as _init
from chimera.commands.project.add import add as _project_add
from chimera.commands.project.ls import projects as _projects
from chimera.commands.project.rm import remove as _project_remove
from chimera.commands.worktree.add import add as _worktree_add
from chimera.commands.worktree.rm import remove as _worktree_remove
from chimera.context import Project, resolve_goal, resolve_project, resolve_workspace
from chimera.worktrees import ACTORS, AGENT, goals, session_name, worktree_dirs, worktree_path

# Reusable option types — declared once, shared across commands (callables never see them).
ProjectOpt = Annotated[
    str | None, typer.Option('--project', '-p', help='Project name (default: inferred from cwd)')
]
GoalOpt = Annotated[
    str | None, typer.Option('--goal', '-g', help='Goal (default: inferred from cwd/branch)')
]
ActorOpt = Annotated[str | None, typer.Option('--actor', '-a', help='Actor (default: agent)')]
FromOpt = Annotated[
    str | None, typer.Option('--from', help='Start ref (default: newest of main/origin/main)')
]
PromptOpt = Annotated[
    str | None, typer.Option('--prompt', help='Prompt; its presence runs the agent in background')
]
ForceOpt = Annotated[bool, typer.Option('--force')]


@dataclass
class Overrides:
    """Context flags (-p/-g/-a) collected from any level of the command line."""

    project: str | None = None
    goal: str | None = None
    actor: str | None = None


def _context(
    ctx: typer.Context, project: ProjectOpt = None, goal: GoalOpt = None, actor: ActorOpt = None
) -> None:
    """Merge any -p/-g/-a given at this level into the shared overrides on ctx.obj.

    Registered as the callback on the root app and every project-scoped group, so the
    flags are accepted before the group, before the command, or after it. Click shares
    one ctx.obj down the chain; each level overwrites only what it was given, so the more
    specific (later) position wins — and a leaf's own flag wins over all of them.
    """
    overrides = ctx.ensure_object(Overrides)
    if project is not None:
        overrides.project = project
    if goal is not None:
        overrides.goal = goal
    if actor is not None:
        overrides.actor = actor


def _overrides(ctx: typer.Context) -> Overrides:
    return ctx.ensure_object(Overrides)


def _project(ctx: typer.Context, explicit: str | None) -> Project:
    return resolve_project(
        Path.cwd(), explicit if explicit is not None else _overrides(ctx).project
    )


app = typer.Typer(callback=_context, help='Chimera — AI agent orchestration.')


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
    anchor = (path or Path.cwd()).resolve()
    root = resolve_root(path, Path.cwd(), os.environ.get('CHIMERA_WORKSPACE'))
    if root != anchor:
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


worktree_app = typer.Typer(callback=_context, help="Manage a goal's worktrees and branches.")
app.add_typer(worktree_app, name='worktree')


@worktree_app.command('add')
def worktree_add(
    ctx: typer.Context,
    goal: Annotated[str, typer.Argument()],
    actors: Annotated[
        list[str] | None, typer.Argument(help='Actors (default: human, agent)')
    ] = None,
    frm: FromOpt = None,
    project: ProjectOpt = None,
) -> None:
    p = _project(ctx, project)
    for created in _worktree_add(
        p.repo, p.worktrees, goal, tuple(actors) if actors else ACTORS, frm
    ):
        typer.echo(f'Created {created}')


@worktree_app.command('rm')
def worktree_rm(
    ctx: typer.Context,
    goal: Annotated[str, typer.Argument()],
    force: ForceOpt = False,
    project: ProjectOpt = None,
) -> None:
    p = _project(ctx, project)
    _report_removed(_worktree_remove(p.repo, p.worktrees, goal, force), goal)


@worktree_app.command('ls')
def worktree_ls(ctx: typer.Context, project: ProjectOpt = None) -> None:
    for worktree in worktree_dirs(_project(ctx, project).worktrees):
        typer.echo(worktree)


goal_app = typer.Typer(callback=_context, help='Work on goals.')
app.add_typer(goal_app, name='goal')


@goal_app.command('start')
def goal_start(
    ctx: typer.Context,
    goal: Annotated[str, typer.Argument()],
    prompt: Annotated[
        str | None,
        typer.Argument(help='Prompt; its presence runs the agent in background'),
    ] = None,
    frm: FromOpt = None,
    project: ProjectOpt = None,
) -> None:
    p = _project(ctx, project)
    worktree = _goal_start(
        p.repo, p.worktrees, goal, session_name(p.name, goal, AGENT), prompt, frm
    )
    typer.echo(f'Started {goal} in {worktree}')


@goal_app.command('finish')
def goal_finish(
    ctx: typer.Context,
    goal: Annotated[str, typer.Argument()],
    force: ForceOpt = False,
    project: ProjectOpt = None,
) -> None:
    p = _project(ctx, project)
    _report_removed(_worktree_remove(p.repo, p.worktrees, goal, force), goal)


@goal_app.command('ls')
def goal_ls(ctx: typer.Context, project: ProjectOpt = None) -> None:
    for goal in sorted(goals(_project(ctx, project).worktrees)):
        typer.echo(goal)


agent_app = typer.Typer(callback=_context, help='Launch and list agents.')
app.add_typer(agent_app, name='agent')


@agent_app.command('start')
def agent_start(
    ctx: typer.Context,
    goal: GoalOpt = None,
    actor: ActorOpt = None,
    prompt: PromptOpt = None,
    project: ProjectOpt = None,
) -> None:
    overrides = _overrides(ctx)
    p = _project(ctx, project)
    g = resolve_goal(Path.cwd(), p, goal if goal is not None else overrides.goal)
    actor = actor or overrides.actor or AGENT
    worktree = worktree_path(p.worktrees, g, actor)
    _agent(worktree, session_name(p.name, g, actor), prompt)
    typer.echo(f'Launched agent in {worktree}')


@agent_app.command('ls')
def agent_ls() -> None:
    listing = agents()
    if not listing:
        typer.echo('No agents running')
        return
    name_w = max(len(a.name) for a in listing)
    status_w = max(len(a.status) for a in listing)
    for a in listing:
        typer.echo(f'{a.name:<{name_w}}  {a.status:<{status_w}}  {a.detail}'.rstrip())


def _report_removed(removed: list[Path], goal: str) -> None:
    for worktree in removed:
        typer.echo(f'Removed {worktree}')
    if not removed:
        typer.echo(f'Nothing to remove for {goal}')


if __name__ == '__main__':  # pragma: no cover
    app()
