import json
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from loguru import logger
from typer._click.core import Command, Context
from typer._click.shell_completion import CompletionItem
from typer.core import TyperCommand, TyperGroup
from typer.main import get_command

from chimera import logging
from chimera.agent_env import (
    RESTRICTED_COMMANDS,
    RESTRICTED_OPTIONS,
    ROLE_AGENT,
    ROLE_CAPTAIN,
    ROLE_COMMANDS,
    ROLE_MANAGER,
    ai_session,
    refuse_cross_scope,
    session_role,
)
from chimera.agents import AgentSession
from chimera.agents.context import Source, assemble, materialize
from chimera.agents.registry import AGENTS, AgentSpec, resolve_spec
from chimera.archive import archive
from chimera.commands.agent import agents, reconcile, resume_target, scope_line, scoped, shown
from chimera.commands.agent import agent as _agent
from chimera.commands.agent import resume as _resume
from chimera.commands.agent import stop as _agent_stop
from chimera.commands.archive.backfill import CLAUDE_PROJECTS
from chimera.commands.archive.backfill import backfill as _archive_backfill
from chimera.commands.chat import chat as _chat
from chimera.commands.dashboard import render as render_dashboard
from chimera.commands.chat import chat_target
from chimera.commands.doctor import REPAIR, Exclusions, Finding, resolve_root, select_checks
from chimera.commands.doctor import checks as doctor_checks
from chimera.commands.doctor import doctor as _doctor
from chimera.commands.dump import dump as _dump
from chimera.commands.errand import errand as _errand
from chimera.commands.goal.adopt import adopt as _goal_adopt
from chimera.commands.goal.ls import goals_in_scope
from chimera.commands.goal.merge import merge as _goal_merge
from chimera.commands.goal.pr import pr as _goal_pr
from chimera.commands.goal.rename import rename as _goal_rename
from chimera.commands.goal.start import start as _goal_start
from chimera.commands.goal.sync import Outcome, SyncResult
from chimera.commands.goal.sync import sync as _goal_sync
from chimera.commands.hook.capture import KNOWN_END_KEYS
from chimera.commands.session.show import show as _session_show
from chimera.commands.session.whoami import whoami as _session_whoami
from chimera.commands.hook.capture import session_end as _hook_session_end
from chimera.commands.hook.capture import session_start as _hook_session_start
from chimera.commands.hook.deliver import deliver as _hook_deliver
from chimera.commands.init import init as _init
from chimera.commands.logtail import logtail as _logtail
from chimera.commands.ls import Board, Mail, Row, board
from chimera.commands.msg.dispose import dispose as _msg_dispose
from chimera.commands.msg.drain import as_context as _msg_as_context
from chimera.commands.msg.drain import drain as _msg_drain
from chimera.commands.msg.inbox import inbox as _msg_inbox
from chimera.commands.msg.ls import outstanding as _msg_outstanding
from chimera.commands.msg.send import send as _msg_send
from chimera.commands.msg.store import mail
from chimera.commands.msg.thread import thread as _msg_thread
from chimera.commands.msg.watch import line as _msg_line
from chimera.commands.msg.watch import watch as _msg_watch
from chimera.commands.project.add import add as _project_add
from chimera.commands.project.checkout import checkout as _project_checkout
from chimera.commands.project.ls import projects as _projects
from chimera.commands.project.new import new as _project_new
from chimera.commands.project.push import push as _project_push
from chimera.commands.project.rm import remove as _project_remove
from chimera.commands.prompt import Prompt
from chimera.commands.prompt import resolve as _prompt_resolve
from chimera.commands.prompt.edit import edit as _prompt_edit
from chimera.commands.prompt.init import init as _prompt_init
from chimera.commands.prompt.ls import prompts as _prompts
from chimera.commands.review import review as _review
from chimera.commands.worktree.add import add as _worktree_add
from chimera.commands.worktree.ls import ls as _worktree_ls
from chimera.commands.worktree.rm import remove as _worktree_remove
from chimera.completions import (
    complete_actor,
    complete_check,
    complete_goal,
    complete_harness,
    complete_project,
    complete_remote,
    complete_template,
)
from chimera.config import NotInWorkspaceError, UserError, workspace_config
from chimera.context import (
    Project,
    Scope,
    resolve_goal,
    resolve_project,
    resolve_scope,
    resolve_workspace,
    seat,
)
from chimera.dry import Dry
from chimera.git import completing
from chimera.help import command_index, render_json, render_text
from chimera.prime import prime as _prime
from chimera.prime import resolve_role
from chimera.addresses import Actor, Captain
from chimera.processes import process_ancestry as _process_ancestry
from chimera.worktrees import (
    AGENT,
    SEP,
    require_valid_actor,
    require_valid_goal,
    worktree_path,
)

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
    str | None,
    typer.Option(
        '--move',
        help='Actor branch to move (default: human; inferred from --to when it names the '
        "goal's only other actor)",
        autocompletion=complete_actor,
    ),
]
ToOpt = Annotated[
    str | None,
    typer.Option(
        '--to',
        help='Actor branch to catch up to (default: agent; inferred from --move when it names '
        "the goal's only other actor)",
        autocompletion=complete_actor,
    ),
]
WorktreeForceOpt = Annotated[
    bool,
    typer.Option(
        '--force',
        help='Stop any live agent sessions, skip the dirty/unmerged safety checks and '
        'fetch; discards uncommitted or unmerged work',
    ),
]
ProjectForceOpt = Annotated[
    bool,
    typer.Option(
        '--force',
        help='Force-finish every goal in the project (discarding unmerged/uncommitted work '
        'per goal); the live-agent check is never skipped',
    ),
]
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
IntoOpt = Annotated[
    str | None,
    typer.Option('--into', help='Branch to land the goal on (default: the repo default branch)'),
]
MergeForceOpt = Annotated[
    bool,
    typer.Option(
        '--force',
        help='Land the newest-committed actor branch even when the actors have diverged, '
        'discarding the others; skips the dirty-worktree check too (the fast-forward rule '
        'is never forced)',
    ),
]
MergeDryOpt = Annotated[
    bool,
    typer.Option('--dry', help='Preview the merge, agent stop and sweep; change nothing'),
]
DraftOpt = Annotated[bool, typer.Option('--draft', help='Open the PR as a draft')]
PrToOpt = Annotated[
    str | None,
    typer.Option(
        '--to',
        help='Remote to push the goal branch to (default: config pr.remote, then origin); '
        'the PR still targets the base on origin, so a non-origin remote opens a cross-repo PR',
        autocompletion=complete_remote,
    ),
]
PrDryOpt = Annotated[
    bool,
    typer.Option('--dry', help='Preview the push and PR, title and body included; change nothing'),
]
StopDryOpt = Annotated[
    bool,
    typer.Option('--dry', help='Preview which sessions would be stopped; change nothing'),
]
DangerousOpt = Annotated[
    bool,
    typer.Option(
        '--dangerous',
        help='Make bypass-permissions mode reachable via shift-tab, dropping auto-accept from '
        'the cycle. AGENTS: never pass this on your own — only with explicit user instruction.',
    ),
]
HarnessOpt = Annotated[
    str | None,
    typer.Option(
        '--harness',
        help='Harness to launch (default: config cascade, then claude)',
        autocompletion=complete_harness,
    ),
]
ModelOpt = Annotated[
    str | None,
    typer.Option(
        '--model',
        '-m',
        help="Model for the session (default: config cascade, then the harness's own)",
    ),
]
LaunchDryOpt = Annotated[
    bool,
    typer.Option(
        '--dry', help='Preview the launch — harness, prompt, injected context — changing nothing'
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

    An action given a goal (``goal start``/``finish``, ``worktree add``, an explicit ``-g``)
    has it contextualized over the whole invoke, so the frames *and* every line logged in
    between carry ``goal`` without any call site binding it.
    """

    def invoke(self, ctx: Context) -> object:
        logging.configure()
        command = _action(ctx)
        goal = ctx.params.get('goal')
        with logger.contextualize(**({'goal': goal} if goal else {})):
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
                logging.log_finish(command, started)  # non-zero exit is an outcome, not a crash
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
    """The project an action targets, held to the session's scope fence.

    The single funnel every project-scoped *action* resolves through, so the fence
    (``refuse_cross_scope``) checks the project actually resolved — an explicit
    cross-scope ``-p`` and a cwd in another project refuse identically. Listers
    resolve through ``_scope`` instead and are never fenced.
    """
    project = resolve_project(
        Path.cwd(), explicit if explicit is not None else _overrides(ctx).project
    )
    refuse_cross_scope(Path.cwd(), project.name)
    return project


def _foreign(ctx: typer.Context, name: str) -> Project:
    """The project an errand dispatches *into* — deliberately not :func:`_project`.

    The scope fence guards the "who I act as" axis; an errand's positional names a
    *target* whose whole point is being foreign, so it resolves unfenced — an axis,
    not a hole: the verb's narrow semantics (one-shot, the read-only tool wall and
    the ephemeral-worktree sweep) are the containment.
    An inherited ``-p`` is refused rather than ignored — silently acting on the
    positional while a flag said otherwise would be worse than either behaviour.
    Exactly one caller — the errand command; a test pins that, so a second caller
    can't quietly turn the exemption into a general escape hatch.
    """
    if _overrides(ctx).project is not None:
        raise UserError('errand names its target positionally — drop -p')
    return resolve_project(Path.cwd(), name)


def _spec(project: Project, harness: str | None, model: str | None) -> AgentSpec:
    """The agent to launch: flags, then the project's ``agent:``, then the workspace's.

    A project is usable without a workspace around it (independence), so the workspace
    level simply drops out of the cascade when none resolves.
    """
    try:
        workspace = workspace_config(resolve_workspace(Path.cwd())).agent
    except NotInWorkspaceError:
        workspace = None
    return resolve_spec(harness, model, project.config.agent, workspace)


def _pr_remote(project: Project, to: str | None) -> str | None:
    """The remote ``goal pr`` pushes to: the flag, then the project's ``pr:``, then the
    workspace's — :func:`_spec`'s cascade shape. None (no level set) leaves the pure
    function's own origin default in charge; a project standing alone (no workspace)
    just loses the workspace level.
    """
    if to is not None:
        return to
    if project.config.pr.remote is not None:
        return project.config.pr.remote
    try:
        return workspace_config(resolve_workspace(Path.cwd())).pr.remote
    except NotInWorkspaceError:
        return None


def _agent_intro(project: str, goal: str) -> str:
    """Errand's affirmative identity line — what the session is, never a prohibition.

    The working launchers push the whole agent prime instead; errand alone keeps the
    bare sentence, since the prime's commit-as-you-go golden path would contradict its
    read-only wall.
    """
    return (
        f'You are the agent for goal {goal} on {project}; '
        'this worktree and branch are your entire workspace.'
    )


def _context_file(
    project: Project | None, name: str, role: str, intro: str
) -> tuple[Path | None, tuple[Source, ...]]:
    """Render and store session ``name``'s launch context; ``(None, ())`` when there is none.

    Role directives + the ``intro`` identity block lead the render (every chimera-launched
    session knows what it is before anything else), then principles and knowledge. The
    render needs a workspace both for the role/workspace-level sources and as the home of
    the stored artifact (``state/context/``), so a project standing outside any workspace
    launches without injected context rather than failing. The sources searched ride back
    for the ``--dry`` preview.
    """
    try:
        workspace = resolve_workspace(Path.cwd())
    except NotInWorkspaceError:
        return None, ()
    rendered = assemble(workspace, project, role, intro)
    return materialize(workspace, name, rendered), rendered.sources


def _dry_preview(
    spec: AgentSpec,
    prompt: str | None,
    extra: list[str],
    context: Path | None,
    address: str,
    *,
    sources: tuple[Source, ...] = (),
    target: str | None = None,
    out: Path | None = None,
) -> None:
    """What a --dry launch would do: the address it records, agent, prompt, passthrough, context.

    ``sources`` lists each glob the render searched with its match count — a ``(0)`` is
    the silent dead end the preview exists to reveal: a directive in the wrong place, or
    a layer with nothing in it. ``target``/``out`` are errand's extra axes — the project
    dispatched into and where the report would land (stdout when ``out`` is None); the
    other launchers leave them off.
    """
    if target is not None:
        typer.echo(f'target: {target}')
        typer.echo(f'out: {out}' if out is not None else 'out: (stdout)')
    typer.echo(f'harness: {spec.harness}' + (f'  model: {spec.model}' if spec.model else ''))
    # the address is what a real launch would put on record for the session to claim —
    # and therefore its role and its fence, which the address already encodes
    typer.echo(f'address: {address}')
    typer.echo(f'prompt: {prompt}' if prompt is not None else 'prompt: (interactive)')
    if extra:
        typer.echo(f'passthrough: {" ".join(extra)}')
    if sources:
        typer.echo('sources:')
        for source in sources:
            typer.echo(f'  {source.pattern} ({len(source.matched)})')
    if context is None:
        typer.echo('context: (none)')
    else:
        typer.echo(f'context: {context}\n---')
        typer.echo(context.read_text().rstrip())


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
def init(
    path: Annotated[Path, typer.Argument()],
    captain: Annotated[
        str | None,
        typer.Option('--captain', help="Name the workspace's captain persona (e.g. pegasus)"),
    ] = None,
) -> None:
    typer.echo(f'Initialized workspace at {_init(path, captain)}')


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


@app.command(
    'prime',
    cls=LoggingCommand,
    help='How to work here, right now — the golden path for this scope.',
)
@logs(_prime)
def prime(ctx: typer.Context) -> None:
    scope = _scope(ctx, None, None)
    typer.echo(
        _prime(
            resolve_role(session_role(Path.cwd()), scope),
            workspace=scope.workspace.name,
            project=scope.project.name if scope.project else None,
            goal=scope.goal,
            persona=workspace_config(scope.workspace).captain.name,
        )
    )


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


@app.command(cls=LoggingCommand, help="Tail the workspace's action log, colourised (via fblog).")
@logs(_logtail)
def logtail(
    lines: Annotated[int, typer.Option('--lines', '-n', help='Initial lines to show')] = 20,
    follow: Annotated[
        bool, typer.Option('--follow/--no-follow', help='Keep following new lines')
    ] = True,
    dump: Annotated[
        bool,
        typer.Option(
            '--dump', '-d', help='Show every field of every record (params, git refs, tracebacks)'
        ),
    ] = False,
) -> None:
    code = _logtail(resolve_workspace(Path.cwd()), lines=lines, follow=follow, dump=dump)
    if code:
        raise typer.Exit(code)


@app.command(
    cls=LoggingCommand,
    help='Log a debug snapshot (cwd, pid, process ancestry, argv, env, stdin payload) — '
    'for chasing hook/environment problems by hand or wired temporarily into a hooks.json '
    'entry.',
)
@logs(_dump)
def dump(
    context: Annotated[
        str | None, typer.Argument(help='Label: a hook event name, or free text (optional)')
    ] = None,
    stdout: Annotated[
        bool, typer.Option('--stdout', help='Also print the captured snapshot to stdout')
    ] = False,
) -> None:
    pid = os.getpid()
    record = _dump(
        context,
        Path.cwd(),
        pid,
        os.getppid(),
        _process_ancestry(pid),
        sys.argv,
        dict(os.environ),
        None if sys.stdin.isatty() else sys.stdin.read(),
    )
    if stdout:
        typer.echo(json.dumps(record, indent=2, default=str))
    else:
        typer.echo(f'dumped: {context}' if context is not None else 'dumped')


@app.command(
    'ls', cls=LoggingCommand, help='Show the workspace dashboard (projects → goals → agents).'
)
@logs(board)
def ls(ctx: typer.Context, project: ProjectOpt = None, goal: GoalOpt = None) -> None:
    scope = _scope(ctx, project, goal, infer=False)  # a bad -p refuses before the registry is hit
    listing = agents()
    # reconcile sees the *whole* listing, the display only the live half: a stale-marked
    # entry is one the registry still claims, so closing its row would contradict the
    # authority reconciliation rests on — and would depend on which lister you happened
    # to run, since `agent ls` passes the unfiltered listing
    reconcile(scope.workspace, listing)  # sessions that died unheard stop looking open
    rows, _ = shown(listing, verbose=False)  # live-only: ghosts are agent ls -v's surface
    with archive(scope.workspace) as store:
        _render_board(board(scope, rows, store, mail(scope.workspace)))


@app.command(
    'dashboard',
    cls=LoggingCommand,
    help='A colorized, columnar workspace dashboard for a human terminal (pair with watch -c).',
)
@logs(board)
def dashboard_cmd(ctx: typer.Context, project: ProjectOpt = None, goal: GoalOpt = None) -> None:
    scope = _scope(ctx, project, goal, infer=False)  # a bad -p refuses before the registry is hit
    listing = agents()
    reconcile(scope.workspace, listing)  # the whole listing, as in `ls` above
    rows, _ = shown(listing, verbose=False)  # live-only: ghosts are agent ls -v's surface
    with archive(scope.workspace) as store:
        # color=True: this command exists to run under `watch`, which pipes our stdout
        # (no tty) — Click would otherwise auto-strip the ANSI codes before watch sees them.
        typer.echo(render_dashboard(board(scope, rows, store, mail(scope.workspace))), color=True)


# Detail (session title / last prompt) past this many chars is trimmed for listings.
DETAIL_MAX = 80


def _name(a: AgentSession) -> str:
    """The session's name, blanked when it merely echoes the id column."""
    return '' if a.name == a.id else a.name


def _detail(a: AgentSession) -> str:
    """The session's one-line detail, trimmed to ``DETAIL_MAX`` with an ellipsis.

    A stale row's detail is its reason — the mark is what the row is showing.
    """
    detail = a.stale if a.stale is not None else a.detail
    return detail if len(detail) <= DETAIL_MAX else detail[: DETAIL_MAX - 1] + '…'


def _status(a: AgentSession) -> str:
    """The status column: ``stale`` displaces the registry's claim on a marked row."""
    return 'stale' if a.stale is not None else a.status


def _summary(a: AgentSession) -> str:
    """``id  name  status  detail`` for a live session, dropping the name when blank."""
    return '  '.join(part for part in (a.short, _name(a), a.status, _detail(a)) if part)


def _mail_summary(m: Mail) -> str:
    """``mail 2n 1c``, terse — omitted when nothing's outstanding; ``done`` never shown
    (matches ``msg ls``'s own default of hiding disposed messages)."""
    parts = [f'{m.new}n'] if m.new else []
    parts += [f'{m.cur}c'] if m.cur else []
    return f'mail {" ".join(parts)}' if parts else ''


def _row_summary(row: Row) -> str:
    """``address  id  status  detail`` for a board row, else ``address  (never run)`` — the
    address (the archive's own name for the slot) always leads, so a configured persona
    (e.g. a workspace's ``--captain pegasus``) is never lost behind a generic label. The
    mail summary is appended when there's anything outstanding.
    """
    if row.live is not None:
        fields = (row.address, row.live.short, _status(row.live), _detail(row.live))
    elif row.last is not None:
        s = row.last
        fields = (row.address, s.native_id[:8], s.status)
    else:
        fields = (row.address, '(never run)')
    base = '  '.join(part for part in fields if part)
    tail = _mail_summary(row.mail)
    return f'{base}  {tail}' if tail else base


def _render_board(b: Board) -> None:
    typer.echo(b.workspace)
    typer.echo(f'  {_row_summary(b.captain)}')
    for p in b.projects:
        typer.echo(f'  {p.name}')
        typer.echo(f'    {_row_summary(p.manager)}')
        for g in p.goals:
            typer.echo(f'    {g.name}')
            for row in g.actors:
                typer.echo(f'      {_row_summary(row)}')
        for a in p.loose:
            typer.echo(f'    · {_summary(a)}')
        if not p.goals and not p.loose:
            typer.echo('    (no goals)')
    for a in b.loose:
        typer.echo(f'  · {_summary(a)}')
    for row in b.history:
        typer.echo(f'  · {_row_summary(row)}')
    if b.history_withheld:
        typer.echo('  (+more archived sessions not shown — ch dashboard for the full view)')


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
    review_step: Annotated[
        str | None,
        typer.Option(
            '--review', help='What to do to gather the diff — fills $REVIEW in the template'
        ),
    ] = None,
    no_agent: Annotated[
        bool,
        typer.Option('--no-agent', help='Branch, fetch and check out the PR, but launch no agent'),
    ] = False,
    harness: HarnessOpt = None,
    model: ModelOpt = None,
    dry: LaunchDryOpt = False,
    project: ProjectOpt = None,
) -> None:
    p = _project(ctx, project)
    dry_run = Dry(dry)
    spec = _spec(p, harness, model)
    context: Path | None = None
    sources: tuple[Source, ...] = ()

    def _render_context(name: str, goal: str) -> Path | None:
        # keyed by the session name and goal _review resolves (pr-<N>, even from a URL
        # argument); the handles are kept so --dry shows the artifact rendered exactly once
        nonlocal context, sources
        context, sources = _context_file(
            p, name, ROLE_AGENT, _prime(ROLE_AGENT, project=p.name, goal=goal)
        )
        return context

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
        review_step=review_step,
        spec=spec,
        context=_render_context,
        dry=dry_run,
    )
    if no_agent:
        goal = worktree.name.split(SEP, 1)[0]
        typer.echo(f'{dry_run.verb("Prepared", "Would prepare")} review of {pr} in {worktree}')
        typer.echo(
            f'ch agent start -g {goal} launches an agent there; '
            f'ch review {goal.removeprefix("pr-")} runs the standard review'
        )
    else:
        typer.echo(f'{dry_run.verb("Reviewing", "Would review")} {pr} in {worktree}')
        if dry:
            template = _source(_prompt_resolve(p.prompts, 'review'))
            filled = f' + --review {review_step!r}' if review_step is not None else ''
            _dry_preview(
                spec,
                f'review template ({template}) + guardrail{filled}',
                _passthrough(ctx),
                context,
                str(Actor(p.name, worktree.name.split(SEP, 1)[0], AGENT)),
                sources=sources,
            )


@app.command(
    'errand',
    cls=PassthroughCommand,
    help='Dispatch a one-shot read-only agent into a project; print or --out its report.',
)
@logs(_errand)
def errand(
    ctx: typer.Context,
    target: Annotated[
        str,
        typer.Argument(help='Project to dispatch into', autocompletion=complete_project),
    ],
    prompt: Annotated[str, typer.Argument(help='What to research and report on')],
    out: Annotated[
        Path | None,
        typer.Option('--out', help='Write the report here (default: print it to stdout)'),
    ] = None,
    keep: Annotated[
        bool,
        typer.Option('--keep', help="Keep the errand's branch and worktree for inspection"),
    ] = False,
    timeout: Annotated[
        float | None, typer.Option('--timeout', help='Bound the run (seconds)')
    ] = None,
    frm: FromOpt = None,
    offline: OfflineOpt = False,
    harness: HarnessOpt = None,
    model: ModelOpt = None,
    dry: LaunchDryOpt = False,
) -> None:
    p = _foreign(ctx, target)
    dry_run = Dry(dry)
    spec = _spec(p, harness, model)
    context: Path | None = None
    sources: tuple[Source, ...] = ()

    def _render_context(name: str, goal: str) -> Path | None:
        # keyed by the resolved session name and goal: only _errand knows the generated
        # goal — the handles are kept so --dry shows the artifact rendered exactly once
        nonlocal context, sources
        context, sources = _context_file(p, name, ROLE_AGENT, _agent_intro(p.name, goal))
        return context

    result = _errand(
        p.repo,
        p.worktrees,
        p.name,
        prompt,
        out.expanduser() if out else None,
        _passthrough(ctx),
        keep,
        frm=frm,
        fetch=not offline,
        timeout=timeout,
        spec=spec,
        context=_render_context,
        dry=dry_run,
    )
    if dry:
        typer.echo(f'Would run errand {result.goal} in {result.worktree}')
        _dry_preview(
            spec,
            f'{prompt} (guardrail prepended)',
            _passthrough(ctx),
            context,
            str(Actor(p.name, result.goal, AGENT)),
            sources=sources,
            target=p.name,
            out=result.out,
        )
        return
    if result.out is None:
        typer.echo(result.report)
    else:
        typer.echo(f'Wrote report to {result.out}')
    if keep:
        typer.echo(
            f'Kept {result.worktree} — ch goal finish {result.goal} -p {p.name} cleans up',
            err=True,
        )
    elif not result.cleaned:
        typer.echo(
            f'errand left work in {result.worktree} — inspect it; '
            f'ch goal finish {result.goal} -p {p.name} cleans up',
            err=True,
        )


@app.command(
    'chat',
    cls=PassthroughCommand,
    help='Chat at the current scope: the workspace captain or a project.',
)
@logs(_chat)
def chat(
    ctx: typer.Context,
    prompt: PromptArg = None,
    resume: Annotated[
        bool, typer.Option('--resume', '-r', help="Revive the scope's previous chat session")
    ] = False,
    dangerous: DangerousOpt = False,
    harness: HarnessOpt = None,
    model: ModelOpt = None,
    dry: LaunchDryOpt = False,
    project: ProjectOpt = None,
    goal: GoalOpt = None,
) -> None:
    scope = _scope(ctx, project, goal)
    config = workspace_config(scope.workspace)
    # an explicit -g the scope couldn't pin (no project) must still reach the refusal
    cwd, name = chat_target(
        scope, str(Captain()), goal if goal is not None else _overrides(ctx).goal
    )
    if scope.project is None:
        spec = resolve_spec(harness, model, config.captain, config.agent)
        role = ROLE_CAPTAIN
        intro = _prime(ROLE_CAPTAIN, persona=config.captain.name, workspace=scope.workspace.name)
    else:
        spec = resolve_spec(harness, model, scope.project.config.agent, config.agent)
        role = ROLE_MANAGER
        intro = _prime(ROLE_MANAGER, project=scope.project.name)
    # the role's prime leads (identity + golden path, so the session starts knowing the
    # loop instead of pulling `ch prime`), then directives, principles, knowledge index
    rendered = assemble(scope.workspace, scope.project, role, intro)
    dry_run = Dry(dry)
    context = materialize(scope.workspace, name, rendered)
    note = _chat(
        cwd,
        name,
        prompt,
        _passthrough(ctx),
        dangerous,
        spec,
        context,
        resume,
        dry_run,
    )
    verb = dry_run.verb(
        'Resumed' if resume else 'Launched', 'Would resume' if resume else 'Would launch'
    )
    typer.echo(f'{verb} chat {name} in {cwd}')
    if note is not None:
        typer.echo(note)
    if dry:
        _dry_preview(spec, prompt, _passthrough(ctx), context, name, sources=rendered.sources)


project_app = typer.Typer(
    callback=_context, cls=alias_group({'list': 'ls'}), help='Manage projects.'
)
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
    'new',
    cls=LoggingCommand,
    help='Create a workspace-only project: a fresh repo, no remote (graduate with project push).',
)
@logs(_project_new)
def project_new(
    name: Annotated[str, typer.Argument(help='Project name')],
    checkout: Annotated[
        Path | None,
        typer.Option('--checkout', help='Also check out the default branch here'),
    ] = None,
) -> None:
    checkout = checkout.expanduser() if checkout else None
    typer.echo(f'Created {_project_new(resolve_workspace(Path.cwd()), name, checkout)}')
    if checkout is not None:
        typer.echo(f'Checked out at {checkout}')


@project_app.command(
    'push',
    cls=LoggingCommand,
    help='Push the default branch to <url> and track it as origin — graduates a workspace-only '
    'project.',
)
@logs(_project_push)
def project_push(
    ctx: typer.Context,
    url: Annotated[str, typer.Argument(help='Git URL to push to and track as origin')],
    checkout: Annotated[
        Path | None,
        typer.Option('--checkout', help='Also check out the pushed branch here'),
    ] = None,
    dry: Annotated[
        bool, typer.Option('--dry', help='Preview the push and remote wiring; change nothing')
    ] = False,
    project: ProjectOpt = None,
) -> None:
    dry_run = Dry(dry)
    checkout = checkout.expanduser() if checkout else None
    p = _project(ctx, project)
    branch = _project_push(p.repo, url, dry_run, checkout, p.worktrees)
    typer.echo(f'{dry_run.verb("Pushed", "Would push")} {branch} to {url} (origin)')
    if checkout is not None:
        typer.echo(f'{dry_run.verb("Checked out", "Would check out")} at {checkout}')


@project_app.command(
    'checkout',
    cls=LoggingCommand,
    help='Check out a branch (default: the default branch) as a plain worktree at <path>.',
)
@logs(_project_checkout)
def project_checkout(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help='Where to check the branch out')],
    branch: Annotated[
        str | None,
        typer.Option('--branch', help='Branch to check out (default: the default branch)'),
    ] = None,
    offline: OfflineOpt = False,
    project: ProjectOpt = None,
) -> None:
    path = path.expanduser()
    p = _project(ctx, project)
    branch = _project_checkout(p.repo, p.worktrees, path, branch, fetch=not offline)
    typer.echo(f'Checked out {branch} at {path}')


@project_app.command(
    'rm', cls=LoggingCommand, help='Remove a tracked project (refuses while it has goals).'
)
@logs(_project_remove)
def project_rm(
    name: Annotated[str, typer.Argument(autocompletion=complete_project)],
    force: ProjectForceOpt = False,
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


prompt_app = typer.Typer(
    callback=_context,
    cls=alias_group({'list': 'ls'}),
    help='The prompt templates ch review and ch goal pr render.',
)
app.add_typer(prompt_app, name='prompt')

TemplateArg = Annotated[str, typer.Argument(help='Template name', autocompletion=complete_template)]


def _source(prompt: Prompt) -> str:
    """A template's file, marked when it is still the packaged one rather than the project's."""
    return f'{prompt.source}' if prompt.overridden else f'{prompt.source} (packaged)'


@prompt_app.command('ls', cls=LoggingCommand, help='List the templates and where each resolves.')
@logs(_prompts)
def prompt_ls(ctx: typer.Context, project: ProjectOpt = None) -> None:
    for prompt in _prompts(_project(ctx, project).prompts):
        typer.echo(f'{prompt.name:<8} {_source(prompt)}')


@prompt_app.command(
    'show',
    cls=LoggingCommand,
    help='Print a template with its file and what each $hole fills with.',
)
@logs(_prompt_resolve)
def prompt_show(ctx: typer.Context, name: TemplateArg, project: ProjectOpt = None) -> None:
    prompt = _prompt_resolve(_project(ctx, project).prompts, name)
    typer.echo(f'source: {_source(prompt)}')
    if not prompt.overridden:
        typer.echo(f'  ch prompt init {name} copies it into the project to edit')
    typer.echo(f'\n{prompt.text.rstrip()}\n')
    typer.echo('substitutions:')
    for hole in prompt.holes:
        typer.echo(f'  ${hole.name} = {hole.value}' + (f'  ({hole.flag})' if hole.flag else ''))


@prompt_app.command(
    'init',
    cls=LoggingCommand,
    help="Copy a packaged template into the project's prompts/ (never overwrites).",
)
@logs(_prompt_init)
def prompt_init(ctx: typer.Context, name: TemplateArg, project: ProjectOpt = None) -> None:
    prompt, created = _prompt_init(_project(ctx, project).prompts, name)
    typer.echo(f'{"Created" if created else "Already yours:"} {prompt.source}')


@prompt_app.command(
    'edit',
    cls=LoggingCommand,
    help="Edit the project's copy of a template, creating it from the packaged one if absent.",
)
@logs(_prompt_edit)
def prompt_edit(
    ctx: typer.Context,
    name: TemplateArg,
    editor: Annotated[
        str | None, typer.Option('--editor', help='Editor to run (default: $VISUAL, then $EDITOR)')
    ] = None,
    project: ProjectOpt = None,
) -> None:
    typer.echo(f'Edited {_prompt_edit(_project(ctx, project).prompts, name, editor).source}')


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
    force: WorktreeForceOpt = False,
    offline: OfflineOpt = False,
    dry: DryOpt = False,
    project: ProjectOpt = None,
) -> None:
    p = _project(ctx, project)
    dry_run = Dry(dry)
    result = _worktree_remove(p.repo, p.worktrees, goal, force, fetch=not offline, dry=dry_run)
    _report_stopped(result.stopped, dry_run)
    _report_removed(list(result.removed), goal, dry_run)


@worktree_app.command('ls', cls=LoggingCommand, help="List a project's worktrees.")
@logs(_worktree_ls)
def worktree_ls(ctx: typer.Context, project: ProjectOpt = None) -> None:
    for worktree in _worktree_ls(_project(ctx, project).worktrees):
        typer.echo(worktree)


goal_app = typer.Typer(
    callback=_context,
    cls=alias_group({'new': 'start', 'cleanup': 'finish', 'list': 'ls', 'mv': 'rename'}),
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
    harness: HarnessOpt = None,
    model: ModelOpt = None,
    dry: LaunchDryOpt = False,
    project: ProjectOpt = None,
) -> None:
    p = _project(ctx, project)
    require_valid_goal(goal)  # before the session name reaches the context-file path
    dry_run = Dry(dry)
    spec = _spec(p, harness, model)
    context, sources = _context_file(
        p,
        str(Actor(p.name, goal, AGENT)),
        ROLE_AGENT,
        _prime(ROLE_AGENT, project=p.name, goal=goal),
    )
    worktree = _goal_start(
        p.repo,
        p.worktrees,
        goal,
        str(Actor(p.name, goal, AGENT)),
        prompt,
        frm,
        _passthrough(ctx),
        fetch=not offline,
        dangerous=dangerous,
        spec=spec,
        context=context,
        dry=dry_run,
    )
    typer.echo(f'{dry_run.verb("Started", "Would start")} {goal} in {worktree}')
    if dry:
        address = str(Actor(p.name, goal, AGENT))
        _dry_preview(spec, prompt, _passthrough(ctx), context, address, sources=sources)


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
    harness: HarnessOpt = None,
    model: ModelOpt = None,
    dry: LaunchDryOpt = False,
    project: ProjectOpt = None,
) -> None:
    p = _project(ctx, project)
    require_valid_goal(goal)  # before the session name reaches the context-file path
    dry_run = Dry(dry)
    spec = _spec(p, harness, model)
    context, sources = _context_file(
        p,
        str(Actor(p.name, goal, AGENT)),
        ROLE_AGENT,
        _prime(ROLE_AGENT, project=p.name, goal=goal),
    )
    worktree = _goal_adopt(
        p.repo,
        p.worktrees,
        goal,
        str(Actor(p.name, goal, AGENT)),
        prompt,
        _passthrough(ctx),
        dangerous,
        spec,
        context,
        dry_run,
    )
    typer.echo(f'{dry_run.verb("Adopted", "Would adopt")} {goal} in {worktree}')
    if dry:
        address = str(Actor(p.name, goal, AGENT))
        _dry_preview(spec, prompt, _passthrough(ctx), context, address, sources=sources)


@goal_app.command(
    'sync',
    cls=LoggingCommand,
    help='Fast-forward one actor branch up to another, creating it if absent (default: human←agent).',
)
@logs(_goal_sync)
def goal_sync(
    ctx: typer.Context,
    goal: Annotated[str | None, typer.Argument(autocompletion=complete_goal)] = None,
    move: MoveOpt = None,
    to: ToOpt = None,
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


@goal_app.command(
    'rename',
    cls=LoggingCommand,
    help='Rename a goal — its branches, worktrees and sync state; remote branches are only '
    'warned about, never changed.',
)
@logs(_goal_rename)
def goal_rename(
    ctx: typer.Context,
    old: ExistingGoalArg,
    new: Annotated[str, typer.Argument(help='New goal name')],
    project: ProjectOpt = None,
) -> None:
    p = _project(ctx, project)
    result = _goal_rename(p.repo, p.worktrees, old, new, Path.cwd())
    for old_ref, new_ref in result.branches:
        typer.echo(f'Renamed branch {old_ref} to {new_ref}')
    for old_wt, new_wt in result.worktrees:
        typer.echo(f'Moved {old_wt} to {new_wt}')
    for warning in result.warnings:
        typer.echo(f'warning: {warning}')
    if result.cwd_moved_to is not None:
        typer.echo(f'note: your cwd moved — cd {result.cwd_moved_to}')


@goal_app.command(
    'merge',
    cls=LoggingCommand,
    help="Land a finished goal: fast-forward the base branch to its work, stop its agent, "
    'and sweep its branches and worktrees.',
)
@logs(_goal_merge)
def goal_merge(
    ctx: typer.Context,
    goal: ExistingGoalArg,
    into: IntoOpt = None,
    force: MergeForceOpt = False,
    offline: OfflineOpt = False,
    dry: MergeDryOpt = False,
    project: ProjectOpt = None,
) -> None:
    p = _project(ctx, project)
    dry_run = Dry(dry)
    result = _goal_merge(p.repo, p.worktrees, goal, into, force, fetch=not offline, dry=dry_run)
    if result.fastforwarded:
        verb = dry_run.verb('Fast-forwarded', 'Would fast-forward')
        typer.echo(f'{verb} {result.into} to {result.source} ({result.sha})')
    else:
        typer.echo(f'{result.into} already contains {result.source} ({result.sha})')
    for landed in result.landed:
        verb = dry_run.verb('Checked out', 'Would check out')
        typer.echo(f'{verb} {landed.branch} at {landed.where} (was {landed.was})')
    _report_stopped(result.stopped, dry_run)
    _report_removed(list(result.removed), goal, dry_run)


@goal_app.command(
    'pr',
    cls=LoggingCommand,
    help='Publish a finished goal as a pull request: push its work to origin as the goal '
    'name and open the PR; local branches stay.',
)
@logs(_goal_pr)
def goal_pr(
    ctx: typer.Context,
    goal: ExistingGoalArg,
    into: IntoOpt = None,
    draft: DraftOpt = False,
    to: PrToOpt = None,
    offline: OfflineOpt = False,
    dry: PrDryOpt = False,
    project: ProjectOpt = None,
) -> None:
    p = _project(ctx, project)
    dry_run = Dry(dry)
    result = _goal_pr(
        p.repo,
        p.name,
        p.prompts,
        goal,
        into,
        draft,
        to=_pr_remote(p, to),
        fetch=not offline,
        dry=dry_run,
    )
    for ref in result.cleared:
        typer.echo(f'{dry_run.verb("Cleared", "Would clear")} stale {ref}')
    verb = dry_run.verb('Pushed', 'Would push')
    typer.echo(
        f'{verb} {result.source} to {result.remote} as {result.remote_branch} ({result.sha})'
    )
    if result.created:
        typer.echo(f'Opened PR: {result.url}')
    elif result.url is not None:
        typer.echo(f'PR already open: {result.url}')
    else:
        typer.echo(f'Would open a PR against {result.base} (head {result.head}):')
        typer.echo(f'title: {result.title}')
        if result.body:
            typer.echo(result.body)


@goal_app.command('finish', cls=LoggingCommand, help="Remove a goal's worktrees and branches.")
@logs(_worktree_remove)
def goal_finish(
    ctx: typer.Context,
    goal: ExistingGoalArg,
    force: WorktreeForceOpt = False,
    offline: OfflineOpt = False,
    dry: DryOpt = False,
    project: ProjectOpt = None,
) -> None:
    p = _project(ctx, project)
    dry_run = Dry(dry)
    result = _worktree_remove(p.repo, p.worktrees, goal, force, fetch=not offline, dry=dry_run)
    _report_stopped(result.stopped, dry_run)
    _report_removed(list(result.removed), goal, dry_run)


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
    harness: HarnessOpt = None,
    model: ModelOpt = None,
    dry: LaunchDryOpt = False,
    project: ProjectOpt = None,
) -> None:
    overrides = _overrides(ctx)
    p = _project(ctx, project)
    g = resolve_goal(Path.cwd(), p, goal if goal is not None else overrides.goal)
    actor = require_valid_actor(actor or overrides.actor or AGENT)
    worktree = worktree_path(p.worktrees, g, actor)
    name = str(Actor(p.name, g, actor))
    dry_run = Dry(dry)
    spec = _spec(p, harness, model)
    context, sources = _context_file(
        p, name, ROLE_AGENT, _prime(ROLE_AGENT, project=p.name, goal=g)
    )
    _agent(worktree, name, prompt, _passthrough(ctx), dangerous, spec, context, dry_run)
    typer.echo(f'{dry_run.verb("Launched", "Would launch")} agent in {worktree}')
    if dry:
        _dry_preview(spec, prompt, _passthrough(ctx), context, name, sources=sources)


@agent_app.command(
    'resume', cls=PassthroughCommand, help="Revive an agent's most recent session in its worktree."
)
@logs(_resume)
def agent_resume(
    ctx: typer.Context,
    prompt: PromptArg = None,
    goal: GoalOpt = None,
    actor: ActorOpt = None,
    dangerous: DangerousOpt = False,
    harness: HarnessOpt = None,
    model: ModelOpt = None,
    dry: LaunchDryOpt = False,
    project: ProjectOpt = None,
) -> None:
    overrides = _overrides(ctx)
    p = _project(ctx, project)
    g = resolve_goal(Path.cwd(), p, goal if goal is not None else overrides.goal)
    actor = require_valid_actor(actor or overrides.actor or AGENT)
    worktree = worktree_path(p.worktrees, g, actor)
    name = str(Actor(p.name, g, actor))
    dry_run = Dry(dry)
    spec = _spec(p, harness, model)
    context, sources = _context_file(
        p, name, ROLE_AGENT, _prime(ROLE_AGENT, project=p.name, goal=g)
    )
    native = resume_target(Path.cwd(), spec.agent.platform, str(Actor(p.name, g, actor)))
    _resume(worktree, name, prompt, _passthrough(ctx), dangerous, spec, context, dry_run, native)
    typer.echo(f'{dry_run.verb("Resumed", "Would resume")} agent in {worktree}')
    if dry:
        typer.echo(f'session: {native}' if native else 'session: (no archived id — by name)')
        _dry_preview(spec, prompt, _passthrough(ctx), context, name, sources=sources)


@agent_app.command(
    'stop', cls=LoggingCommand, help="Stop the live agent session in a goal's worktree."
)
@logs(_agent_stop)
def agent_stop(
    ctx: typer.Context,
    goal: GoalOpt = None,
    actor: ActorOpt = None,
    dry: StopDryOpt = False,
    project: ProjectOpt = None,
) -> None:
    overrides = _overrides(ctx)
    p = _project(ctx, project)
    g = resolve_goal(Path.cwd(), p, goal if goal is not None else overrides.goal)
    a = require_valid_actor(actor or overrides.actor or AGENT)
    worktree = worktree_path(p.worktrees, g, a)
    dry_run = Dry(dry)
    stopped = _agent_stop(worktree, dry_run)
    _report_stopped(stopped, dry_run)
    if not stopped:
        typer.echo(f'No live agent in {worktree}')


@agent_app.command('ls', cls=LoggingCommand, help='List running agents.')
@logs(scoped)
def agent_ls(
    ctx: typer.Context,
    verbose: Annotated[
        bool,
        typer.Option('--verbose', '-v', help='Also show stale sessions, each with why it is stale'),
    ] = False,
    project: ProjectOpt = None,
    goal: GoalOpt = None,
) -> None:
    scope = _scope(ctx, project, goal)
    typer.echo(scope_line(scope))
    listing = agents()
    reconcile(scope.workspace, listing)
    rows, withheld = shown(scoped(listing, scope, otherwise=None), verbose)
    if not rows:
        typer.echo('No agents running')
    else:
        id_w = max(len(a.short) for a in rows)
        name_w = max(len(_name(a)) for a in rows)
        status_w = max(len(_status(a)) for a in rows)
        for a in rows:
            row = f'{a.short:<{id_w}}  {_name(a):<{name_w}}  {_status(a):<{status_w}}  {_detail(a)}'
            typer.echo(row.rstrip())
    if withheld:
        plural = 's' if withheld != 1 else ''
        typer.echo(f'(+{withheld} stale session{plural} — ch agent ls -v to show)')


msg_app = typer.Typer(cls=alias_group({'list': 'ls'}), help='Inter-agent mail.')
app.add_typer(msg_app, name='msg')


@msg_app.command('ls', cls=LoggingCommand, help='List outstanding inter-agent messages.')
@logs(_msg_outstanding)
def msg_ls(
    verbose: Annotated[
        bool, typer.Option('--verbose', '-v', help='Also show disposed messages awaiting cleanup')
    ] = False,
) -> None:
    located = _msg_outstanding(resolve_workspace(Path.cwd()))
    rows = located if verbose else [(state, m) for state, m in located if state != 'done']
    if not rows:
        typer.echo('No outstanding messages')
    for state, message in rows:
        typer.echo(
            f'{state:<4}  {message.sender} → {message.to}  [{message.kind}] {message.subject}'
        )
    withheld = sum(1 for state, _ in located if state == 'done')
    if withheld and not verbose:
        plural = 's' if withheld != 1 else ''
        typer.echo(f'(+{withheld} disposed message{plural} — ch msg ls -v to show)')


@msg_app.command('send', cls=LoggingCommand, help='Send a message to an actor address.')
@logs(_msg_send)
def msg_send(
    to: Annotated[str, typer.Argument(help='Recipient address, e.g. chimera@fix@agent or pegasus')],
    subject: Annotated[str, typer.Argument()],
    body: Annotated[str, typer.Argument(help='Message body')] = '',
    frm: Annotated[
        str | None, typer.Option('--from', help='Sender address (default: inferred from cwd)')
    ] = None,
    kind: Annotated[
        str, typer.Option('--kind', help='message | request | escalation | notice')
    ] = 'message',
    priority: Annotated[str, typer.Option('--priority', help='normal | urgent')] = 'normal',
    re: Annotated[
        str | None, typer.Option('--re', help='Id of the message this replies to')
    ] = None,
) -> None:
    sender = frm if frm is not None else seat(Path.cwd())
    message = _msg_send(
        resolve_workspace(Path.cwd()),
        sender=sender,
        to=to,
        subject=subject,
        body=body,
        kind=kind,
        priority=priority,
        re=re,
    )
    typer.echo(f'Sent {message.id} to {to}')


@msg_app.command('inbox', cls=LoggingCommand, help='Show the messages awaiting an address.')
@logs(_msg_inbox)
def msg_inbox(
    address: Annotated[
        str | None, typer.Argument(help='Whose inbox (default: inferred from cwd)')
    ] = None,
    unread: Annotated[
        bool, typer.Option('--unread', help='Only the undrained (new) messages')
    ] = False,
) -> None:
    who = address if address is not None else seat(Path.cwd())
    messages = _msg_inbox(resolve_workspace(Path.cwd()), who, unread_only=unread)
    if not messages:
        typer.echo('No messages')
    for message in messages:
        typer.echo(f'{message.id}  {message.sender}  [{message.kind}] {message.subject}')


@msg_app.command('thread', cls=LoggingCommand, help='Show a whole conversation by its root id.')
@logs(_msg_thread)
def msg_thread(
    root: Annotated[str, typer.Argument(help='Root message id of the thread')],
    address: Annotated[
        str | None, typer.Argument(help='Whose mailbox (default: inferred from cwd)')
    ] = None,
) -> None:
    who = address if address is not None else seat(Path.cwd())
    messages = _msg_thread(resolve_workspace(Path.cwd()), who, root)
    if not messages:
        typer.echo('No such thread')
    for message in messages:
        typer.echo(
            f'{message.id}  {message.sender} → {message.to}  [{message.kind}] {message.subject}'
        )


@msg_app.command('ack', cls=LoggingCommand, help='Mark a message handled (retire it).')
@logs(_msg_dispose)
def msg_ack(
    message_id: Annotated[str, typer.Argument(help='Id of the message to retire')],
    address: Annotated[
        str | None, typer.Argument(help='Whose mailbox (default: inferred from cwd)')
    ] = None,
) -> None:
    who = address if address is not None else seat(Path.cwd())
    _msg_dispose(resolve_workspace(Path.cwd()), who, message_id)
    typer.echo(f'Acked {message_id}')


@msg_app.command('defer', cls=LoggingCommand, help='Retire a message, recording why (deferred).')
@logs(_msg_dispose)
def msg_defer(
    message_id: Annotated[str, typer.Argument(help='Id of the message to retire')],
    reason: Annotated[str, typer.Option('--reason', help='Why it is being deferred (logged)')],
    address: Annotated[
        str | None, typer.Argument(help='Whose mailbox (default: inferred from cwd)')
    ] = None,
) -> None:
    who = address if address is not None else seat(Path.cwd())
    _msg_dispose(resolve_workspace(Path.cwd()), who, message_id)
    typer.echo(f'Deferred {message_id}: {reason}')


@msg_app.command('drain', cls=LoggingCommand, help='Claim (receive) new messages for an address.')
@logs(_msg_drain)
def msg_drain(
    address: Annotated[
        str | None, typer.Argument(help='Whose mail to receive (default: inferred from cwd)')
    ] = None,
    inject: Annotated[
        bool, typer.Option('--inject', help='Print as a context block for a turn-boundary hook')
    ] = False,
) -> None:
    who = address if address is not None else seat(Path.cwd())
    claimed = _msg_drain(resolve_workspace(Path.cwd()), who)
    if inject:
        if claimed:
            typer.echo(_msg_as_context(claimed))
    else:
        if not claimed:
            typer.echo('Nothing to receive')
        for message in claimed:
            typer.echo(f'{message.id}  from {message.sender}  [{message.kind}] {message.subject}')


@msg_app.command(
    'watch',
    cls=LoggingCommand,
    help='Follow an inbox: one line per newly arriving message (read-only, never claims).',
)
@logs(_msg_watch)
def msg_watch(
    address: Annotated[
        str | None, typer.Argument(help='Whose inbox (default: inferred from cwd)')
    ] = None,
    interval: Annotated[
        float, typer.Option('--interval', help='Seconds between inbox polls')
    ] = 1.0,
) -> None:
    who = address if address is not None else seat(Path.cwd())
    feed = _msg_watch(resolve_workspace(Path.cwd()), who, interval=interval)
    try:
        for message in feed:  # typer.echo flushes per line — the line-buffered contract
            typer.echo(_msg_line(message))
    except KeyboardInterrupt:
        pass  # the normal way a watch ends — a clean exit, so the end frame still logs


archive_app = typer.Typer(help='The session archive at state/archive.db.')
app.add_typer(archive_app, name='archive')


@archive_app.command(
    'backfill',
    cls=LoggingCommand,
    help="Import claude's pre-hook transcripts into the archive.",
)
@logs(_archive_backfill)
def archive_backfill(
    projects: Annotated[
        Path,
        typer.Option('--projects', help="Claude's transcript store to scan"),
    ] = CLAUDE_PROJECTS,
) -> None:
    result = _archive_backfill(projects)
    typer.echo(
        f'Imported {result.imported} (already archived: {result.present}, '
        f'outside any workspace: {result.outside}, unplaceable: {result.unplaced})'
    )


session_app = typer.Typer(help='Inspect sessions: who this one is, and what ran.')
app.add_typer(session_app, name='session')


@session_app.command(
    'whoami',
    cls=LoggingCommand,
    help='What this session is: its address, or that it holds none.',
)
@logs(_session_whoami)
def session_whoami() -> None:
    typer.echo(_session_whoami(Path.cwd()))


@session_app.command(
    'show', cls=LoggingCommand, help="One session's record and timeline, by id or its start."
)
@logs(_session_show)
def session_show(
    session: Annotated[str, typer.Argument(help='A native session id, or its leading block')],
) -> None:
    typer.echo(_session_show(Path.cwd(), session))


hook_app = typer.Typer(help='Hooks chimera installs into the harness (invoked by it, not you).')
app.add_typer(hook_app, name='hook')


@hook_app.command(
    'session-start', cls=LoggingCommand, help='Record a starting session (SessionStart hook).'
)
@logs(_hook_session_start)
def hook_session_start() -> None:
    # the hooks are installed into claude's own settings, so claude is what fired this;
    # a second harness would install its own hook command naming itself
    _hook_session_start(AGENTS['claude'], json.load(sys.stdin), os.environ)


@hook_app.command('session-end', cls=LoggingCommand, help='Mark a session ended (SessionEnd hook).')
@logs(_hook_session_end)
def hook_session_end() -> None:
    payload = json.load(sys.stdin)
    _hook_session_end(
        Path(str(payload['cwd'])),
        str(payload['session_id']),
        str(payload.get('reason') or 'ended'),
        extra={k: v for k, v in payload.items() if k not in KNOWN_END_KEYS},
    )


@hook_app.command(
    'deliver', cls=LoggingCommand, help='Surface unacked mail to a session (UserPromptSubmit hook).'
)
@logs(_hook_deliver)
def hook_deliver() -> None:
    payload = json.load(sys.stdin)
    delivered = _hook_deliver(Path(str(payload['cwd'])), str(payload['session_id']))
    if delivered:
        typer.echo(_msg_as_context(delivered))


def _report_removed(removed: list[Path], goal: str, dry: Dry = Dry()) -> None:
    verb = dry.verb('Removed', 'Would remove')
    for worktree in removed:
        typer.echo(f'{verb} {worktree}')
    if not removed:
        typer.echo(f'Nothing to remove for {goal}')


def _report_stopped(stopped: Sequence[AgentSession], dry: Dry = Dry()) -> None:
    verb = dry.verb('Stopped', 'Would stop')
    for session in stopped:
        typer.echo(f'{verb} {session.name} (pid {session.pid})')


def _strip_restricted_options(command: Command) -> None:
    """Remove agent-restricted options from the Click tree — not merely hidden, unparseable:
    Click's own parser and ``--help`` no longer know they exist."""
    command.params = [
        p for p in command.params if not RESTRICTED_OPTIONS.intersection(getattr(p, 'opts', ()))
    ]
    for sub in getattr(command, 'commands', {}).values():
        _strip_restricted_options(sub)


def _strip_restricted_commands(command: Command, path: str = '') -> None:
    """Delete the human-only leaves (``RESTRICTED_COMMANDS``) from the Click tree — the
    option strip one level up, applied to *every* AI session, captain included (the role
    allowlists narrow further, but never grant these back). Same absence-not-admonition
    semantics as ``_strip_to_role``, same group-emptying sweep."""
    commands: dict[str, Command] = getattr(command, 'commands', {})
    for name, sub in list(commands.items()):
        if getattr(sub, 'commands', None) is not None:  # a group — prune inside, then itself
            _strip_restricted_commands(sub, f'{path}{name} ')
            if not getattr(sub, 'commands'):
                del commands[name]
        elif f'{path}{name}' in RESTRICTED_COMMANDS:
            del commands[name]


def _strip_to_role(command: Command, allowed: frozenset[str], path: str = '') -> None:
    """Prune the Click tree to the ``allowed`` canonical leaf paths — the option strip one
    level up. A fenced command isn't hidden but absent: parsing, ``--help``, ``ch help`` and
    completion all forget it, a group emptied by the prune is deleted with it, and a synonym
    dies with its canonical target (``alias_group.get_command`` resolves through the pruned
    ``commands`` dict, so nothing is left to dispatch to)."""
    commands: dict[str, Command] = getattr(command, 'commands', {})
    for name, sub in list(commands.items()):
        if getattr(sub, 'commands', None) is not None:  # a group — prune inside, then itself
            _strip_to_role(sub, allowed, f'{path}{name} ')
            if not getattr(sub, 'commands'):
                del commands[name]
        elif f'{path}{name}' not in allowed:
            del commands[name]


def main() -> None:
    # Who this session is decides which tree it gets, so it is resolved before anything
    # parses. There is no longer an unknown-role case to guard: a role is read off the
    # address, whose three shapes are the three roles, so it can only be one of them or
    # nothing at all.
    # loguru ships a stderr sink that would print this resolution's own SQLite trace to
    # the console before any command has run; `configure` drops it again for real once a
    # workspace is known (and for the same reason resolves identity before adding a sink)
    logger.remove()
    try:
        role = session_role(Path.cwd())
        driven_by_agent = ai_session(Path.cwd())
    except UserError as error:
        # a completer must never raise or print (an archive awaiting migration would
        # otherwise break every TAB), so fail closed and complete nothing
        if completing():
            raise SystemExit(0) from None
        # doctor is how a broken workspace gets repaired, so it has to run in one — it
        # proceeds unidentified, and therefore unfenced, which is the same standing a
        # human has. Everything else says what is wrong and stops, here rather than
        # half-way through a command.
        if REPAIR not in sys.argv[1:2]:
            typer.echo(f'Error: {error}', err=True)
            raise SystemExit(1) from None
        role, driven_by_agent = None, False
    if driven_by_agent:
        command = get_command(app)
        if role in ROLE_COMMANDS:  # prune first: the later strips walk the smaller tree
            _strip_to_role(command, ROLE_COMMANDS[role])
        _strip_restricted_commands(command)
        _strip_restricted_options(command)
        command()
    else:
        app()  # a human at a terminal — typer's own path


if __name__ == '__main__':  # pragma: no cover
    main()
