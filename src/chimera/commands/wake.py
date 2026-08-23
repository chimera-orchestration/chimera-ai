"""Waking parked sessions: the lossless revive for a daemon-parked agent."""

from pathlib import Path

from chimera.agents import Agent, Session
from chimera.agents.registry import AGENTS
from chimera.config import UserError
from chimera.dry import Dry


def wake(
    worktree: Path | None,
    target: str | None,
    dry: Dry = Dry(),
    timeout: float = 30.0,
) -> Session:
    """Find the parked session — in ``worktree``, else named by ``target`` — and wake it.

    One function for both shapes (the mode dispatch belongs here, not the CLI wrapper —
    see agent-docs/commands.md): a resolved goal worktree wins; failing that ``target``
    names a session directly (its name, full id, or short id) across every harness.
    Waking goes through the session's own harness (:meth:`chimera.agents.Agent.wake`),
    which verifies the respawn; under ``dry`` nothing runs. Returns the parked session
    that was (or would be) woken.
    """
    if worktree is not None and worktree.is_dir():
        harness, session = _in_worktree(worktree)
    elif target is not None:
        found = _named(target)
        if found is None:
            raise UserError(
                f'{target}: neither a goal with a worktree nor a parked session by that name/id'
            )
        harness, session = found
    else:
        raise UserError('nothing to wake: no goal worktree resolved and no session named')
    dry(harness.wake, session, timeout)
    return session


def _in_worktree(worktree: Path) -> tuple[Agent, Session]:
    """The parked session occupying ``worktree``, with the harness that reported it.

    A live session refuses — there is nothing to wake, attach to it; no session at all
    refuses pointing at ``agent resume``, whose job the truly dead are.
    """
    checked = [(h, s) for h in AGENTS.values() for s in h.checked(worktree)]
    for harness, session in checked:
        if session.parked:
            return harness, session
    if live := [s for _, s in checked if s.stale is None]:
        names = ', '.join(f'{s.short} ({s.status})' for s in live)
        raise UserError(f'nothing parked in {worktree} — already live: {names}; attach to it')
    raise UserError(
        f'nothing parked in {worktree} — a dead session is revived with ch agent resume'
    )


def _named(target: str) -> tuple[Agent, Session] | None:
    """The parked session ``target`` names — by session name, full id, or short id."""
    for harness in AGENTS.values():
        for session in harness.checked():
            if session.parked and target in (session.name, session.id, session.short):
                return harness, session
    return None
