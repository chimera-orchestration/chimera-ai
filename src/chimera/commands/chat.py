from collections.abc import Sequence
from pathlib import Path

from chimera.agent_env import ROLE_MANAGER
from chimera.agents.registry import AgentSpec
from chimera.commands.agent import refuse_restricted
from chimera.config import UserError
from chimera.context import Scope
from chimera.dry import Dry
from chimera.worktrees import SEP


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

    Two scopes chat: a project in its project dir as ``<project>@manager`` — session
    names carry the role at every layer (bare persona / ``<project>@manager`` /
    ``<project>@<goal>@agent``) — and the bare workspace as the captain: its persona
    name *is* the session name, and it works on the workspace as a whole (no goal,
    branch or worktree).

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
    return scope.project.dir, f'{scope.project.name}{SEP}{ROLE_MANAGER}'


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
) -> None:
    """Launch (or with ``resume`` revive) the chat session ``name`` in ``cwd``.

    A chat deliberately sits alongside whatever agent is working there, so the
    harness's one-session-per-cwd guard is off; the guard here is by *name* over the
    live tier (a stale remnant of an old chat never blocks) — the scope's chat already
    being live means attach, not launch, whichever was asked.
    The guards fire under ``dry`` too, so a preview still reports the refusal.
    """
    refuse_restricted(spec, extra)
    if any(session.name == name for session in spec.agent.live()):
        raise ChatAlreadyLiveError(name)
    launch = spec.agent.resume if resume else spec.agent.start
    dry(
        launch,
        cwd,
        name,
        prompt,
        extra,
        dangerous,
        model=spec.model,
        context=context,
        exclusive=False,
    )
