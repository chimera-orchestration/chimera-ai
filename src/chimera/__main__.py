import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from typer._click.core import Command, Context
from typer._click.shell_completion import CompletionItem
from typer.core import TyperCommand, TyperGroup

from chimera import logging
from chimera.commands.agent import Agent, agents, scope_line, scoped
from chimera.commands.agent import agent as _agent
from chimera.commands.agent import resume as _resume
from chimera.commands.doctor import Exclusions, Finding, resolve_root, select_checks
from chimera.commands.doctor import checks as doctor_checks
from chimera.commands.doctor import doctor as _doctor
from chimera.commands.goal.adopt import adopt as _goal_adopt
from chimera.commands.goal.ls import goals_in_scope
from chimera.commands.goal.start import start as _goal_start
from chimera.commands.goal.sync import Outcome, SyncResult
from chimera.commands.goal.sync import sync as _goal_sync
from chimera.commands.init import init as _init
from chimera.commands.ls import Board, board
from chimera.commands.project.add import add as _project_add
from chimera.commands.project.ls import projects as _projects
from chimera.commands.project.rm import remove as _project_remove
from chimera.commands.review import review as _review
from chimera.commands.worktree.add import add as _worktree_add
from chimera.commands.worktree.ls import ls as _worktree_ls
from chimera.commands.worktree.rm import remove as _worktree_remove
from chimera.completions import complete_actor, complete_check, complete_goal, complete_project
from chimera.config import UserError
from chimera.context import (
    Project,
    Scope,
    resolve_goal,
    resolve_project,
    resolve_scope,
    resolve_workspace,
)
from chimera.dry import Dry
from chimera.help import command_index, render_json, render_text
from chimera.worktrees import AGENT, HUMAN, session_name, worktree_path

# Reusable option types — declared once, shared across commands (callables never see them).
ProjectOpt = Annotated[
    str | None,
    typer.Option(
        '--project',
        '-p',
        help='Project name (default: inferred from cwd)',
        autocompletion=complete_project,
    ),
]
GoalOpt = Annotated[
    str | None,
    typer.Option(
        '--goal',
        '-g',
        help='Goal (default: inferred from cwd/branch)',
        autocompletion=complete_goal,
    ),
]
ActorOpt = Annotated[
    str | None,
    typer.Option('--actor', '-a', help='Actor (default: agent)', autocompletion=complete_actor),
]
# A positional naming a goal that already exists (finish/rm); new-goal args stay plain.
ExistingGoalArg = Annotated[str, typer.Argument(autocompletion=complete_goal)]
FromOpt = Annotated[
    str | None,
    typer.Option('--from', help='Start ref (default: newest of <default>/origin/<default>)'),
]
OfflineOpt = Annotated[
    bool, typer.Option('--offline', help="Don't fetch origin first; use the refs already present")
]
PromptArg = Annotated[
    str | None,
    typer.Argument(help='Prompt; its presence runs the agent in background'),
]
MoveOpt = Annotated[
    str,
    typer.Option(
        '--move', help='Actor branch to move (default: human)', autocompletion=complete_actor
    ),
]
ToOpt = Annotated[
    str,
    typer.Option(
        '--to', help='Actor branch to catch up to (default: agent)', autocompletion=complete_actor
    ),
]
ForceOpt = Annotated[bool, typer.Option('--force')]
SyncForceOpt = Annotated[
    bool,
    typer.Option(
        '--force',
        help='On divergence, repoint the mover onto the target, discarding the mover-only '
        'commits (shas recoverable from the log)',
    ),
]
DryOpt = Annotated[
    bool, typer.Option('--dry', help='Preview what would be removed; change nothing')
]
DangerousOpt = Annotated[
    bool,
    typer.Option(
        '--dangerous',
        help='Make bypass-permissions mode reachable via shift-tab, dropping auto-accept from '
        'the cycle. AGENTS: never pass this on your own — only with explicit user instruction.',
    ),
]


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


def alias_group(aliases: dict[str, str]) -> type[TyperGroup]:
    """Build a group class whose synonyms resolve to canonical commands.

    A synonym dispatches to the real command but never shows in --help, and the
    command that runs is always the canonical one — so only the canonical name is
    logged. It does tab-complete, though, so a typed synonym can be finished.
    Add synonyms by extending the dict: alias_group({'new': 'start'}).
    See agent-docs/commands.md.
    """

    class AliasGroup(TyperGroup):
        synonyms = aliases

        def get_command(self, ctx: Context, cmd_name: str) -> Command | None:
            return super().get_command(ctx, aliases.get(cmd_name, cmd_name))

        def shell_complete(self, ctx: Context, incomplete: str) -> list[CompletionItem]:
            # list_commands stays canonical (so --help/logging don't see synonyms);
            # completion alone offers them, each carrying its target's help.
            completions = super().shell_complete(ctx, incomplete)
            completions.extend(
                CompletionItem(synonym, help=command.get_short_help_str())
                for synonym, canonical in aliases.items()
                if synonym.startswith(incomplete)
                and (command := self.get_command(ctx, canonical)) is not None
                and not command.hidden
            )
            return completions

    return AliasGroup


def logs[F: Callable[..., object]](function: Callable[..., object]) -> Callable[[F], F]:
    """Tag a command wrapper with the pure function it delegates to, so the start line can
    log that function's dotted path. The wrapper alone only ever resolves to
    ``chimera.__main__.*``; apply this *under* the command decorator so the tag is set before
    typer copies the wrapper's ``__dict__``. A test asserts every command carries one.
    """

    def decorate(wrapper: F) -> F:
        qualname = getattr(function, '__qualname__')  # every command delegate is a function
        wrapper.__dict__['delegate'] = f'{function.__module__}.{qualname}'
        return wrapper

    return decorate


class LoggingCommand(TyperCommand):
    """A command that logs a start/end pair around the action it runs.

    The chokepoint for the *"every CLI action must be logged"* principle: it configures the
    loguru sink, logs a start line (the canonical command path, the delegate's dotted path —
    see :func:`logs` — and the parsed params), runs the command, then an end line with the
    duration. A crash makes the end line an ERROR carrying the traceback; an expected
    ``UserError`` gets a one-line message (not typer's rich traceback) and an ERROR end line
    with the message but no traceback; a ``typer.Exit``/``Abort`` is normal control flow, so it
    still ends cleanly. Synonyms are already resolved to their canonical command by the time
    ``invoke`` is reached, so the logged name is always canonical.
    """

    def invoke(self, ctx: Context) -> object:
        logging.configure()
        command = _action(ctx)
        started = logging.log_start(
            command, getattr(ctx.command.callback, 'delegate'), dict(ctx.params)
        )
        try:
            result = super().invoke(ctx)
        except UserError as error:
            typer.echo(f'Error: {error}', err=True)
            logging.log_user_error(command, started, error)
            raise typer.Exit(1) from error
        except (typer.Exit, typer.Abort):
            logging.log_finish(command, started)  # a non-zero exit is an outcome, not a crash
            raise
        except Exception:
            logging.log_failure(command, started)
            raise
        else:
            logging.log_finish(command, started)
            return result


def _action(ctx: Context) -> str:
    """The canonical command path without the program name (e.g. ``project ls``).

    The last segment of ``command_path`` is the name as typed, so a synonym would log
    itself; replacing it with the resolved command's own name logs the canonical command
    it dispatches to (``goal new`` → ``goal start``).
    """
    segments = ctx.command_path.split(' ')
    segments[-1] = ctx.command.name or segments[-1]
    return ' '.join(segments[1:])


class PassthroughCommand(LoggingCommand):
    """A command that forwards everything after ``--`` to the underlying binary, verbatim.

    Click otherwise lets a declared positional (the prompt) swallow the first post-``--``
    token, so ``ch agent start -- --dangerously-skip-permissions`` would mistake the flag
    for the prompt. We split on ``--`` ourselves (as git/cargo do) before Click parses:
    the tail is stashed on ``ctx.meta`` and the head alone fills the command's own params.
    """

    def parse_args(self, ctx: Context, args: list[str]) -> list[str]:
        if '--' in args:
            cut = args.index('--')
            ctx.meta['passthrough'] = args[cut + 1 :]
            args = args[:cut]
        else:
            ctx.meta['passthrough'] = []
        return super().parse_args(ctx, args)


def _passthrough(ctx: typer.Context) -> list[str]:
    """The args given after ``--``, forwarded straight to claude (see ``PassthroughCommand``)."""
    return ctx.meta.get('passthrough', [])


def _project(ctx: typer.Context, explicit: str | None) -> Project:
    return resolve_project(
        Path.cwd(), explicit if explicit is not None else _overrides(ctx).project
    )


def _scope(
    ctx: typer.Context, project: str | None, goal: str | None, *, infer: bool = True
) -> Scope:
    overrides = _overrides(ctx)
    return resolve_scope(
        Path.cwd(),
        project=project if project is not None else overrides.project,
        goal=goal if goal is not None else overrides.goal,
        infer=infer,
    )


app = typer.Typer(
    callback=_context,
    cls=alias_group({'list': 'ls'}),
    help='Chimera — AI agent orchestration.',
)


@app.command(cls=LoggingCommand, help='Create a Chimera workspace at PATH.')
@logs(_init)
def init(path: Annotated[Path, typer.Argument()]) -> None:
    typer.echo(f'Initialized workspace at {_init(path)}')


@app.command(
    'help', cls=LoggingCommand, help='List every command in one chunk (derived from the live tree).'
)
@logs(command_index)
def help_(
    ctx: typer.Context,
    verbose: Annotated[
        bool, typer.Option('--verbose', '-v', help="Also show each command's options and synonyms")
    ] = False,
    as_json: Annotated[bool, typer.Option('--json', help='Emit the index as JSON')] = False,
) -> None:
    entries = command_index(ctx.find_root().command)
    typer.echo(render_json(entries) if as_json else render_text(entries, verbose=verbose))


@app.command(cls=LoggingCommand, help='Check and (with --fix) repair workspace health.')
@logs(_doctor)
def doctor(
    path: Annotated[Path | None, typer.Argument()] = None,
    fix: Annotated[
        bool, typer.Option('--fix', help='Apply the fixes instead of only reporting')
    ] = False,
    check: Annotated[
        list[str] | None,
        typer.Option(
            '--check',
            '-c',
            help='Run only the named checks (repeatable; default: all)',
            autocompletion=complete_check,
        ),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option(
            '--exclude',
            '-x',
            help='Skip findings matching (check name, or message substring; repeatable)',
            autocompletion=complete_check,
        ),
    ] = None,
    verbose: Annotated[
        bool, typer.Option('--verbose', '-v', help='Show every check, including the ones that pass')
    ] = False,
) -> None:
    selected = select_checks(check or ())
    exclusions = Exclusions(tuple(exclude or ()))
    anchor = (path or Path.cwd()).resolve()
    root = resolve_root(path, Path.cwd(), os.environ.get('CHIMERA_WORKSPACE'))
    if root != anchor:
        typer.echo(f'note: resolved workspace root: {root}')
    if verbose:
        repo = doctor_checks.chimera_repo()
        if repo is not None:
            typer.echo(f'note: chimera checkout: {repo}')
    findings = _doctor(root, fix, selected, exclusions)
    by_check: dict[str, list[Finding]] = {}
    for finding in findings:
        by_check.setdefault(finding.check, []).append(finding)
    for selected_check in selected:
        reported = by_check.get(selected_check.name)
        if reported:
            for finding in reported:
                typer.echo(f'[{finding.check}] ({_tag(finding)}) {finding.message}')
        elif verbose:
            typer.echo(f'[{selected_check.name}] (ok)')
    passing = sum(1 for selected_check in selected if selected_check.name not in by_check)
    for token in exclusions.unmatched:
        typer.echo(f'warning: -x {token!r} matched nothing')
    if not findings and not exclusions.excluded:
        if verbose:
            typer.echo('All checks passed!')
        else:
            typer.echo(f'All checks passed! (ch doctor -v lists the {passing} checks run)')
    elif passing and not verbose:
        typer.echo(f'(+{passing} checks passed — ch doctor -v to list)')
    if count := exclusions.excluded:
        typer.echo(f'({count} finding{"s" if count != 1 else ""} excluded by -x)')
    if any(not finding.resolved for finding in findings):
        raise typer.Exit(1)


def _tag(finding: Finding) -> str:
    if finding.resolved:
        return 'fixed'
    return 'would fix — run with --fix' if finding.fixable else 'needs attention'


@app.command(
    'ls', cls=LoggingCommand, help='Show the workspace dashboard (projects → goals → agents).'
)
@logs(board)
def ls(ctx: typer.Context, project: ProjectOpt = None, goal: GoalOpt = None) -> None:
    _render_board(board(_scope(ctx, project, goal, infer=False), agents()))


# Detail (session title / last prompt) past this many chars is trimmed for listings.
DETAIL_MAX = 80


def _name(a: Agent) -> str:
    """The agent's name, blanked when it merely echoes the id column."""
    return '' if a.name == a.id else a.name


def _detail(a: Agent) -> str:
    """The agent's one-line detail, trimmed to ``DETAIL_MAX`` with an ellipsis."""
    return a.detail if len(a.detail) <= DETAIL_MAX else a.detail[: DETAIL_MAX - 1] + '…'


def _summary(a: Agent) -> str:
    """``id  name  status  detail`` for a board row, dropping the name when blank."""
    return '  '.join(part for part in (a.id, _name(a), a.status, _detail(a)) if part)


def _render_board(b: Board) -> None:
    typer.echo(b.workspace)
    for p in b.projects:
        typer.echo(f'  {p.name}')
        for g in p.goals:
            if g.agents:
                typer.echo(f'    {g.name}')
                for a in g.agents:
                    typer.echo(f'      {_summary(a)}')
            else:
                typer.echo(f'    {g.name}  (no agent)')
        for a in p.loose:
            typer.echo(f'    · {_summary(a)}')
        if not p.goals and not p.loose:
            typer.echo('    (no goals)')
    for a in b.loose:
        typer.echo(f'  · {_summary(a)}')


@app.command(
    'review',
    cls=PassthroughCommand,
    help='Open a pre-human review of a PR: goal + worktree tracking the PR, then an agent.',
)
@logs(_review)
def review(
    ctx: typer.Context,
    pr: Annotated[
        str,
        typer.Argument(
            help='PR number, or URL — github or any review tool naming owner/repo and number'
        ),
    ],
    dangerous: DangerousOpt = False,
    no_agent: Annotated[
        bool,
        typer.Option('--no-agent', help='Branch, fetch and check out the PR, but launch no agent'),
    ] = False,
    project: ProjectOpt = None,
) -> None:
    p = _project(ctx, project)
    worktree = _review(
        p.repo,
        p.worktrees,
        p.name,
        p.prompts,
        pr,
        _passthrough(ctx),
        dangerous,
        Path.cwd(),
        launch=not no_agent,
    )
    typer.echo(f'{"Prepared review of" if no_agent else "Reviewing"} {pr} in {worktree}')


project_app = typer.Typer(cls=alias_group({'list': 'ls'}), help='Manage projects.')
app.add_typer(project_app, name='project')


@project_app.command(
    'add', cls=LoggingCommand, help='Track a project — clone a git URL or register a local path.'
)
@logs(_project_add)
def project_add(
    source: Annotated[str, typer.Argument(help='Git URL to clone, or local path to track')],
    checkout: Annotated[
        Path | None,
        typer.Option(
            '--checkout', help='Also check out the default branch here (URL sources only)'
        ),
    ] = None,
) -> None:
    checkout = checkout.expanduser() if checkout else None
    typer.echo(f'Added {_project_add(resolve_workspace(Path.cwd()), source, checkout)}')
    if checkout is not None:
        typer.echo(f'Checked out at {checkout}')


@project_app.command(
    'rm', cls=LoggingCommand, help='Remove a tracked project (refuses while it has goals).'
)
@logs(_project_remove)
def project_rm(
    name: Annotated[str, typer.Argument(autocompletion=complete_project)],
    force: ForceOpt = False,
    dry: DryOpt = False,
) -> None:
    dry_run = Dry(dry)
    removed = _project_remove(resolve_workspace(Path.cwd()), name, force, dry_run)
    if removed:
        typer.echo(f'{dry_run.verb("Removed", "Would remove")} {removed}')
    else:
        typer.echo(f'No project named {name} to remove')


@project_app.command('ls', cls=LoggingCommand, help='List tracked projects.')
@logs(_projects)
def project_ls() -> None:
    for name in _projects(resolve_workspace(Path.cwd())):
        typer.echo(name)


worktree_app = typer.Typer(
    callback=_context,
    cls=alias_group({'list': 'ls'}),
    help="Manage a goal's worktrees and branches.",
)
app.add_typer(worktree_app, name='worktree')


@worktree_app.command(
    'add',
    cls=LoggingCommand,
    help="Check out a branch as a worktree — an ad-hoc <branch> [path], or a goal's actors "
    '(--goal).',
)
@logs(_worktree_add)
def worktree_add(
    ctx: typer.Context,
    branch: Annotated[str | None, typer.Argument(help='Branch to check out (ad-hoc mode)')] = None,
    path: Annotated[Path | None, typer.Argument(help='Where to check it out (ad-hoc mode)')] = None,
    goal: Annotated[
        str | None,
        typer.Option('--goal', '-g', help='Goal to create actor branches/worktrees for'),
    ] = None,
    actor: Annotated[
        list[str] | None,
        typer.Option(
            '--actor',
            '-a',
            help='Actor for --goal (repeatable; default: agent)',
            autocompletion=complete_actor,
        ),
    ] = None,
    frm: FromOpt = None,
    offline: OfflineOpt = False,
    project: ProjectOpt = None,
) -> None:
    p = _project(ctx, project)
    for created in _worktree_add(
        p.repo,
        p.worktrees,
        goal=goal,
        actors=tuple(actor) if actor else None,
        branch=branch,
        path=path.expanduser() if path else None,
        frm=frm,
        fetch=not offline,
    ):
        typer.echo(f'Created {created}')


@worktree_app.command('rm', cls=LoggingCommand, help="Remove a goal's worktrees and branches.")
@logs(_worktree_remove)
def worktree_rm(
    ctx: typer.Context,
    goal: ExistingGoalArg,
    force: ForceOpt = False,
    offline: OfflineOpt = False,
    dry: DryOpt = False,
    project: ProjectOpt = None,
) -> None:
    p = _project(ctx, project)
    dry_run = Dry(dry)
    _report_removed(
        _worktree_remove(p.repo, p.worktrees, goal, force, fetch=not offline, dry=dry_run),
        goal,
        dry_run,
    )


@worktree_app.command('ls', cls=LoggingCommand, help="List a project's worktrees.")
@logs(_worktree_ls)
def worktree_ls(ctx: typer.Context, project: ProjectOpt = None) -> None:
    for worktree in _worktree_ls(_project(ctx, project).worktrees):
        typer.echo(worktree)


goal_app = typer.Typer(
    callback=_context,
    cls=alias_group({'new': 'start', 'cleanup': 'finish', 'list': 'ls'}),
    help='Work on goals.',
)
app.add_typer(goal_app, name='goal')


@goal_app.command(
    'start',
    cls=PassthroughCommand,
    help="Branch, create the worktree, and launch the goal's agent.",
)
@logs(_goal_start)
def goal_start(
    ctx: typer.Context,
    goal: Annotated[str, typer.Argument()],
    prompt: PromptArg = None,
    frm: FromOpt = None,
    offline: OfflineOpt = False,
    dangerous: DangerousOpt = False,
    project: ProjectOpt = None,
) -> None:
    p = _project(ctx, project)
    worktree = _goal_start(
        p.repo,
        p.worktrees,
        goal,
        session_name(p.name, goal, AGENT),
        prompt,
        frm,
        _passthrough(ctx),
        fetch=not offline,
        dangerous=dangerous,
    )
    typer.echo(f'Started {goal} in {worktree}')


@goal_app.command(
    'adopt',
    cls=PassthroughCommand,
    help='Bring an existing branch under goal management and launch its agent.',
)
@logs(_goal_adopt)
def goal_adopt(
    ctx: typer.Context,
    goal: Annotated[str, typer.Argument(help='Existing branch to adopt as a goal')],
    prompt: PromptArg = None,
    dangerous: DangerousOpt = False,
    project: ProjectOpt = None,
) -> None:
    p = _project(ctx, project)
    worktree = _goal_adopt(
        p.repo,
        p.worktrees,
        goal,
        session_name(p.name, goal, AGENT),
        prompt,
        _passthrough(ctx),
        dangerous,
    )
    typer.echo(f'Adopted {goal} in {worktree}')


@goal_app.command(
    'sync',
    cls=LoggingCommand,
    help='Fast-forward one actor branch up to another, creating it if absent (default: human←agent).',
)
@logs(_goal_sync)
def goal_sync(
    ctx: typer.Context,
    goal: Annotated[str | None, typer.Argument(autocompletion=complete_goal)] = None,
    move: MoveOpt = HUMAN,
    to: ToOpt = AGENT,
    force: SyncForceOpt = False,
    project: ProjectOpt = None,
) -> None:
    overrides = _overrides(ctx)
    p = _project(ctx, project)
    g = resolve_goal(Path.cwd(), p, goal if goal is not None else overrides.goal)
    result = _goal_sync(p.repo, g, move, to, Path.cwd(), force)
    typer.echo(_sync_line(result))
    if result.outcome is Outcome.CONFLICT:
        raise typer.Exit(1)  # append left mid-conflict for the human to finish


def _sync_line(result: SyncResult) -> str:
    """What ``goal sync`` did: the outcome, plus a checkout line when it landed in place."""
    mover, target, sha = result.mover, result.target, result.sha
    match result.outcome:
        case Outcome.CREATED:
            line = f'Created {mover} at {target} ({sha})'
        case Outcome.NOOP:
            line = f'{mover} already has everything from {target} ({sha})'
        case Outcome.FASTFORWARDED:
            line = f'Fast-forwarded {mover} to {target} ({sha})'
        case Outcome.AHEAD:
            line = f'{mover} leads {target} by {result.ahead_by} — nothing to sync'
        case Outcome.APPENDED:
            line = f'Appended {result.appended} commit(s) from {target} onto {mover} ({sha})'
        case Outcome.REPOINTED:
            line = f'Repointed {mover} onto {target} ({sha}) — tips already matched exactly'
        case Outcome.FORCED:
            line = (
                f'Forced {mover} onto {target} ({sha}) — discarded {result.discarded} '
                f'commit(s), shas in the log'
            )
        case Outcome.CONFLICT:
            return (
                f'Conflict appending {target} onto {mover} — resolve in {result.conflict}, '
                f'`git cherry-pick --continue`, then re-run'
            )
    if (c := result.checkout) is None:
        return line
    if c.done:
        return f'{line}\nChecked out {c.branch} here' + (f' (was {c.was})' if c.was else '')
    return (
        f'{line}\n(note: uncommitted changes — {c.branch} not checked out; '
        f'commit/stash then `git checkout {c.branch}`)'
    )


@goal_app.command('finish', cls=LoggingCommand, help="Remove a goal's worktrees and branches.")
@logs(_worktree_remove)
def goal_finish(
    ctx: typer.Context,
    goal: ExistingGoalArg,
    force: ForceOpt = False,
    offline: OfflineOpt = False,
    dry: DryOpt = False,
    project: ProjectOpt = None,
) -> None:
    p = _project(ctx, project)
    dry_run = Dry(dry)
    _report_removed(
        _worktree_remove(p.repo, p.worktrees, goal, force, fetch=not offline, dry=dry_run),
        goal,
        dry_run,
    )


@goal_app.command('ls', cls=LoggingCommand, help='List goals.')
@logs(goals_in_scope)
def goal_ls(ctx: typer.Context, project: ProjectOpt = None) -> None:
    scope = _scope(ctx, project, None)
    for proj, goal in goals_in_scope(scope):
        typer.echo(goal if scope.project is not None else f'{proj}  {goal}')


agent_app = typer.Typer(
    callback=_context, cls=alias_group({'list': 'ls'}), help='Launch and list agents.'
)
app.add_typer(agent_app, name='agent')


@agent_app.command(
    'start', cls=PassthroughCommand, help='Launch an agent session in a goal worktree.'
)
@logs(_agent)
def agent_start(
    ctx: typer.Context,
    prompt: PromptArg = None,
    goal: GoalOpt = None,
    actor: ActorOpt = None,
    dangerous: DangerousOpt = False,
    project: ProjectOpt = None,
) -> None:
    overrides = _overrides(ctx)
    p = _project(ctx, project)
    g = resolve_goal(Path.cwd(), p, goal if goal is not None else overrides.goal)
    actor = actor or overrides.actor or AGENT
    worktree = worktree_path(p.worktrees, g, actor)
    _agent(worktree, session_name(p.name, g, actor), prompt, _passthrough(ctx), dangerous)
    typer.echo(f'Launched agent in {worktree}')


@agent_app.command('resume', cls=PassthroughCommand, help="Reattach to an agent's session.")
@logs(_resume)
def agent_resume(
    ctx: typer.Context,
    prompt: PromptArg = None,
    goal: GoalOpt = None,
    actor: ActorOpt = None,
    dangerous: DangerousOpt = False,
    project: ProjectOpt = None,
) -> None:
    overrides = _overrides(ctx)
    p = _project(ctx, project)
    g = resolve_goal(Path.cwd(), p, goal if goal is not None else overrides.goal)
    actor = actor or overrides.actor or AGENT
    worktree = worktree_path(p.worktrees, g, actor)
    _resume(worktree, session_name(p.name, g, actor), prompt, _passthrough(ctx), dangerous)
    typer.echo(f'Resumed agent in {worktree}')


@agent_app.command('ls', cls=LoggingCommand, help='List running agents.')
@logs(scoped)
def agent_ls(ctx: typer.Context, project: ProjectOpt = None, goal: GoalOpt = None) -> None:
    scope = _scope(ctx, project, goal)
    typer.echo(scope_line(scope))
    listing = scoped(agents(), scope, otherwise=None)
    if not listing:
        typer.echo('No agents running')
        return
    id_w = max(len(a.id) for a in listing)
    name_w = max(len(_name(a)) for a in listing)
    status_w = max(len(a.status) for a in listing)
    for a in listing:
        row = f'{a.id:<{id_w}}  {_name(a):<{name_w}}  {a.status:<{status_w}}  {_detail(a)}'
        typer.echo(row.rstrip())


def _report_removed(removed: list[Path], goal: str, dry: Dry = Dry()) -> None:
    verb = dry.verb('Removed', 'Would remove')
    for worktree in removed:
        typer.echo(f'{verb} {worktree}')
    if not removed:
        typer.echo(f'Nothing to remove for {goal}')


if __name__ == '__main__':  # pragma: no cover
    app()
