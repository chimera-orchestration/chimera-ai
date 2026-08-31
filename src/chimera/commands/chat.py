from collections.abc import Sequence
from pathlib import Path
from typing import cast

from loguru import logger

from chimera.addresses import Manager
from chimera.agents import Launch
from chimera.agents.registry import AgentSpec
from chimera.commands.agent import record_launch, refuse_restricted
from chimera.config import UserError
from chimera.context import Scope
from chimera.dry import Dry


class ChatAlreadyLiveError(UserError):
    def __init__(self, name: str) -> None:
        super().__init__(f"chat '{name}' is already live — attach to it, or stop it first")


class GoalHasAgentError(UserError):
    def __init__(self, goal: str, project: str | None = None) -> None:
        super().__init__(
            f'a goal has its agent — ch agent resume -g {goal} talks to it; '
            f'ch chat from the {project or "<project>"} dir for a side conversation'
        )


def chat_target(scope: Scope, captain: str, goal: str | None = None) -> tuple[Path, str]:
    """Where a chat at ``scope`` runs and what its session is named.

    Two scopes chat: a project in its project dir as ``<project>@@manager`` (see
    ``chimera.addresses``) and the bare workspace as the captain: ``captain`` is its
    technical address (``@@captain`` — its persona is a separate, cosmetic identity,
    never the session name), and it works on the workspace as a whole (no goal, branch
    or worktree).

    A goal never chats: it already has its agent, and a second session on the same
    branch/worktree invites launch-order traps and lifecycle interference — so a pinned
    goal refuses, pointing at both real options. ``goal`` carries an explicitly
    requested goal that scope resolution couldn't pin (a ``-g`` with no project): asked
    for is asked for, so it refuses the same way rather than being swallowed. The
    refusal precedes any Dry routing, so ``--dry`` still reports it.
    """
    requested = scope.goal if scope.goal is not None else goal
    if requested is not None:
        raise GoalHasAgentError(requested, scope.project.name if scope.project else None)
    if scope.project is None:
        return scope.workspace, captain
    return scope.project.dir, str(Manager(project=scope.project.name))


def chat(
    cwd: Path,
    name: str,
    prompt: str | None = None,
    extra: Sequence[str] = (),
    dangerous: bool = False,
    spec: AgentSpec = AgentSpec(),
    context: Path | None = None,
    resume: bool = False,
    dry: Dry = Dry(),
) -> str | None:
    """Launch (or with ``resume`` revive) the chat session ``name`` in ``cwd``.

    A chat deliberately sits alongside whatever agent is working there, so the
    harness's one-session-per-cwd guard is off; the guard here is by *name* over the
    live tier (a stale remnant of an old chat never blocks) — the scope's chat already
    being live means attach, not launch, whichever was asked. Under ``dry`` that guard
    reports instead of refusing — the returned note, echoed with the preview: a preview
    mutates nothing, and the scope's chat being live is its normal state, so refusing
    would make the preview unreachable exactly when it's wanted (the other launchers'
    liveness checks live inside the launch the ``dry`` switch already skips).
    """
    refuse_restricted(cwd, spec, extra)
    live = any(session.name == name for session in spec.agent.live())
    if live and not dry.on:
        raise ChatAlreadyLiveError(name)
    # cast, not an annotation: ty narrows the local back to the start|resume union at the
    # call site, and the union's ParamSpec join trips over resume's extra kw-only `id`
    launch = cast(Launch, spec.agent.resume if resume else spec.agent.start)
    if not resume:  # a resume takes nothing new — see chimera.commands.agent.resume
        dry(record_launch, cwd, name, spec)
    dry(
        launch,
        cwd,
        name,
        prompt,
        extra,
        dangerous,
        model=spec.model,
        context=context,
    )
    if live:
        logger.bind(session=name).warning('chat: already live')
        return f"note: chat '{name}' is already live — a real launch would refuse"
    return None
