from pathlib import Path
from typing import cast, get_args

from chimera.agent_env import ROLE_AGENT, ROLE_MANAGER
from chimera.commands.msg.store import mail
from chimera.comms import Kind, Message, Priority, compose
from chimera.config import UserError
from chimera.worktrees import SEP

_ROLE_SHAPES: dict[str, str] = {
    ROLE_MANAGER: f'{{project}}{SEP}{ROLE_MANAGER}',
    ROLE_AGENT: f'{{project}}{SEP}<goal>{SEP}{ROLE_AGENT}',
}


def send(
    workspace: Path,
    *,
    sender: str,
    to: str,
    subject: str,
    body: str,
    kind: str,
    priority: str,
    re: str | None,
) -> Message:
    """Send a message from ``sender`` to ``to``; ``re`` (a message id) makes it a reply.

    A reply carries the replied-to id as both its ``re`` and its conversation ``thread``, so
    ``ch msg thread`` gathers the exchange. ``kind``/``priority`` are validated here — an
    unknown value is a :class:`UserError`, not a crash.

    A bare role token as ``to`` (``manager``, ``agent``) is refused with the qualified form
    hinted — concrete when ``sender``'s own address carries a project, the shape otherwise.
    Silently resolving the role against the sender's project was considered and rejected as
    too magical: addressing stays explicit. The refusal is for known role tokens only — a
    captain's address is a bare *persona* name and must keep working — and only new sends
    refuse: an existing bare-role mailbox stays readable (inbox/ack/defer/thread), so past
    strandings can still be dealt with.
    """
    if to in _ROLE_SHAPES:
        project = sender.split(SEP, 1)[0] if SEP in sender else '<project>'
        hint = _ROLE_SHAPES[to].format(project=project)
        raise UserError(
            f'{to!r} is a bare role — its mailbox is a dead letter no session reads; send to {hint}'
        )
    if kind not in get_args(Kind):
        raise UserError(f'unknown kind {kind!r}; one of {", ".join(get_args(Kind))}')
    if priority not in get_args(Priority):
        raise UserError(f'unknown priority {priority!r}; one of {", ".join(get_args(Priority))}')
    return mail(workspace).send(
        compose(
            sender=sender,
            to=to,
            kind=cast(Kind, kind),
            subject=subject,
            body=body,
            priority=cast(Priority, priority),
            thread=re,
            re=re,
        )
    )
