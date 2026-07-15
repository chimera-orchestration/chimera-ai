"""Re-arm enforcement at the idle boundary: the Stop hook.

The wake watcher (``ch msg watch --once`` as a background task) is spent the moment it
fires, and re-arming it is an instruction the session must act on every wake — advisory,
so one interrupted or forgetful turn leaves the session idle and deaf, which nothing can
then reach (there is no external wake path; gastown hit this exact deadlock and needed an
external poller with terminal access). The Stop hook is chimera's better option: it runs
at the end of the very turn that forgot, the one moment the failure exists but the
session hasn't yet gone idle. When an addressed session tries to stop with no live
watcher holding its address, the stop is blocked once, with the re-arm instruction as
the reason — making the re-arm structural instead of advisory, at a cost paid only on
the failure path.

Blocked *once*: the harness marks a continuation turn with ``stop_hook_active``, and that
always passes — a session that still won't (or can't) re-arm is let go rather than
trapped. Fail-open everywhere else too: sessions the archive marked unaddressed (a
one-shot ``-p``, an errand) are never blocked, a cwd outside any workspace passes, and
any error in the check itself allows the stop (WARNING, not a trap) — a broken hook must
never hold a session hostage.
"""

from pathlib import Path

from loguru import logger

from chimera.commands.hook.capture import archive
from chimera.commands.msg.watch import armed
from chimera.config import NotInWorkspaceError
from chimera.context import caller, resolve_workspace


def stop(cwd: Path, session: str, *, stop_hook_active: bool = False) -> str | None:
    """The block reason when ``session`` may not idle yet (unwatched address), else None.

    ``stop_hook_active`` marks the continuation of a stop this hook already blocked —
    always allowed, so a block can never loop.
    """
    if stop_hook_active:
        return None
    try:
        workspace = resolve_workspace(cwd)
        address = caller(cwd)
        with archive(workspace) as store:
            recorded = store.session('claude', session)
        if recorded is not None and recorded.name is None:
            return None  # unaddressed (-p one-shot, errand) — never nagged, never trapped
        if armed(workspace, address):
            return None
    except NotInWorkspaceError:
        return None
    except Exception as error:
        logger.bind(session=session, error=repr(error)).warning(
            'hook stop: check failed, allowing the stop'
        )
        return None
    logger.bind(session=session, address=address).info('hook stop: blocked an unwatched idle')
    return (
        f'No mail watcher is armed for {address}: run `ch msg watch --once` in the '
        'background as a task, then finish — without one, mail cannot wake this session '
        'once idle.'
    )
