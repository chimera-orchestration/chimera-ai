from collections.abc import Sequence
from pathlib import Path

from chimera.agents.registry import AgentSpec
from chimera.config import UserError
from chimera.context import Scope
from chimera.dry import Dry
from chimera.worktrees import AGENT, SEP, session_name, worktree_path

CHAT = 'chat'
"""The pseudo-actor naming project- and goal-scoped chat sessions."""


class ChatAlreadyLiveError(UserError):
    def __init__(self, name: str) -> None:
        super().__init__(f"chat '{name}' is already live — attach to it, or stop it first")


def chat_target(scope: Scope, captain: str) -> tuple[Path, str]:
    """Where a chat at ``scope`` runs and what its session is named.

    The narrowest pinned axis decides: a goal chats in that goal's agent worktree as
    ``<project>@<goal>@chat``, a project in its project dir as ``<project>@chat``, and
    the bare workspace as the captain — its persona name *is* the session name, and it
    works on the workspace as a whole (no goal, branch or worktree).
    """
    if scope.project is None:
        return scope.workspace, captain
    if scope.goal is None:
        return scope.project.dir, f'{scope.project.name}{SEP}{CHAT}'
    return (
        worktree_path(scope.project.worktrees, scope.goal, AGENT),
        session_name(scope.project.name, scope.goal, CHAT),
    )


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
    harness's one-session-per-cwd guard is off; the guard here is by *name* — the
    scope's chat already being live means attach, not launch, whichever was asked.
    The guard fires under ``dry`` too, so a preview still reports the refusal.
    """
    if any(session.name == name for session in spec.agent.sessions()):
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
