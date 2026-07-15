"""Mail delivery at the turn boundary: the UserPromptSubmit hook.

The injection must surface every unacked message, not just what this call claims itself:
``ch msg drain`` moves mail ``new/`` → ``cur/`` for whoever runs it, so an inject path
that printed only its own claims would go permanently silent about a message some other
process drained — the delivery trap. :meth:`chimera.comms.Comms.deliver` holds the
invariant instead: until acked, a message reaches every session of its recipient, each
exactly once (the per-session ``seen/`` ledger); only ``ch msg ack``/``defer`` end it.

Whether the session *has* an address is the archive's fact, not re-inferred here: a
session its SessionStart recorded without one (a one-shot ``claude -p`` — see
``capture.addressed``) shares a cwd with real conversations, so ``caller(cwd)`` would
happily hand it their mail. A session the archive has no row for still gets its mail —
a real chat must not go silent because a hook misfired.

**The re-arm reminder rides delivery.** Mail only *wakes* an idle session through its own
``ch msg watch --once`` background task (there is no external wake path), and that task is
spent the moment it fires — so the wake turn itself, and any session whose watcher was
lost (a resume revives no tasks; a forgotten re-arm), is running unwatched. This hook is
the one thing guaranteed to run on every turn, so it also answers "am I watched?":
:attr:`Delivery.rearm` is set whenever no live watcher holds the session's address
(:func:`chimera.commands.msg.watch.armed`), and the CLI renders it as a one-line re-arm
instruction — making any turn of a deaf session heal it, including the wake turn, where
the just-exited watcher has by definition dropped its marker. Sessions that must not be
nagged (archived unaddressed — a one-shot ``-p``, an errand) never get the flag.

``verbose`` gates a per-call diagnostic line (off by default — this fires on every turn
of every session, so silence is the norm). It exists for the one behaviour we can only
observe in the field: backgrounding an interactive session *bridges* it onto a fresh
``native_id`` that SessionStart never records, so it reaches this hook as an id the
archive has never seen (``recorded=False``). That is invisible to ``ch agent ls`` and
``ch agent resume``, which still resolve the pre-bridge id — so when a backgrounded
session stops waking, or gets the wrong mail, turning ``-v`` on at the hook (in the
settings.json command line) records session id, cwd, resolved address and whether the
archive knew the session, which is enough to spot a bridge from the log alone.
"""

from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from chimera.commands.hook.capture import archive
from chimera.commands.msg.store import mail
from chimera.commands.msg.watch import armed
from chimera.comms import Message
from chimera.config import NotInWorkspaceError
from chimera.context import caller, resolve_workspace

# What the CLI injects when Delivery.rearm is set — worded for the session reading it.
REARM = (
    'No mail watcher is armed for this session: run `ch msg watch --once` in the '
    'background as a task now — without one, mail cannot wake you once you go idle.'
)


@dataclass(frozen=True)
class Delivery:
    """What one hook call surfaces: messages to inject, and whether to ask for a re-arm."""

    messages: list[Message] = field(default_factory=list)
    rearm: bool = False


def deliver(cwd: Path, session: str, *, verbose: bool = False) -> Delivery:
    """The unacked messages for ``cwd``'s address not yet seen by ``session``, claiming
    them — plus, when no live watcher holds the address, the re-arm flag.

    The hook fires for every session on the machine; outside any workspace there is no
    mailbox to read, and a session archived without a mail address gets none — those are
    the no-ops (never nagged to re-arm either). ``verbose`` emits the session-identity
    diagnostic described in the module docstring; it is off by default because it fires
    on every turn of every session.
    """
    try:
        workspace = resolve_workspace(cwd)
        address = caller(cwd)
    except NotInWorkspaceError:
        return Delivery()
    with archive(workspace) as store:
        recorded = store.session('claude', session)
    if verbose:
        bound = logger.bind(
            session=session,
            cwd=str(cwd),
            address=address,
            recorded=recorded is not None,
            recorded_name=recorded.name if recorded is not None else None,
        )
        # -v gates the volume; the level still carries triage (DEBUG is the git
        # trace's): unrecorded = a fallback taken → WARNING, resolved = normal → INFO.
        if recorded is None:
            bound.warning(
                'hook deliver: no archive row for this session — bridged (a backgrounded '
                'session re-hosted under a new id) or pre-hook; delivering by cwd address'
            )
        else:
            bound.info('hook deliver: session resolved from the archive')
    if recorded is not None and recorded.name is None:
        logger.bind(session=session).info('hook deliver: unaddressed session, no mail')
        return Delivery()
    rearm = not armed(workspace, address)
    if rearm:
        logger.bind(session=session, address=address).info(
            'hook deliver: watcher unarmed, re-arm requested'
        )
    return Delivery(mail(workspace).deliver(address, session), rearm=rearm)
