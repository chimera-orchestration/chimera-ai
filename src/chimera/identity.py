"""Who is running this ``ch`` — established, never assumed.

Three modules answer three different questions about "who", and confusing them is what
this one exists to stop:

- :mod:`chimera.addresses` is the address *value type* — the grammar, not a lookup.
- :mod:`chimera.context` resolves *scope from cwd*: which workspace, project and goal a
  directory belongs to. Location.
- this module resolves *the executor*: which session, if any, is running the command, and
  therefore what it is entitled to be called.

The distinction is the whole point of ``agent-docs/sessions.md``'s rule — *a session is
reached by its address, never by its location*. Standing in an agent's worktree is not
evidence of being that agent; being the session chimera launched there is.

So the chain is evidence-only, and short:

1. the harness says which session this process is inside (:meth:`~chimera.agents.Agent.
   session_id_from_env`) — the one channel that survives a background launch and a bridge;
2. the archive says what that session's address is, if it has one.

Anything else — a human's shell, a hand-launched session, one whose claim expired — is
``None``. There is deliberately no fallback to geography: a wrong answer here routes
another agent's mail.
"""

from pathlib import Path

from chimera.agents.registry import AGENTS
from chimera.archive import ArchiveSession, archive
from chimera.config import NotInWorkspaceError
from chimera.context import resolve_workspace

HUMAN = 'human'
"""What a command with no session behind it is attributed to. Not an address: nothing
routes to it, and it is never a claim about *which* human."""


def current_session(cwd: Path) -> ArchiveSession | None:
    """The archived session this process is running inside, or ``None`` if it isn't one.

    Asks each registered harness whether this is one of its sessions, then looks the
    answer up. Best-effort about the *lookup* — no workspace, no archive, nothing
    recorded — but never about the *evidence*: a session that can't be found stays
    unidentified rather than being guessed at from where it happens to be.
    """
    try:
        workspace = resolve_workspace(cwd)
    except NotInWorkspaceError:
        return None
    with archive(workspace) as store:
        for platform, harness in AGENTS.items():
            native_id = harness.session_id_from_env()
            if native_id is not None:
                return store.session(platform, native_id)
    return None


def executor(cwd: Path) -> str:
    """What to attribute this command to: an address, a session id, or ``human``.

    The log's ``caller``. A session holding an address is named by it; a session without
    one — a hand-launched ``claude``, a browser draft — is named by its short id, which
    says *something ran this and here is which conversation* without implying a claim it
    doesn't have. Neither means a human is at the keyboard, so anything else is
    :data:`HUMAN`.

    This is deliberately not the same question as "whose mailbox is this" (see
    :func:`chimera.context.seat`): a human at the workspace root legitimately acts for
    the captain's mailbox, but the command was still run by a human, and the log should
    say so.
    """
    session = current_session(cwd)
    if session is None:
        return HUMAN
    return session.address or session.native_id[:8]
