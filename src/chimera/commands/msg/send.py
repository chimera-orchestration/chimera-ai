from pathlib import Path
from typing import cast, get_args

from chimera.addresses import Address
from chimera.commands.msg.store import mail
from chimera.comms import Kind, Message, Priority, compose
from chimera.config import UserError


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
    ``ch msg thread`` gathers the exchange. ``kind``/``priority``/``to`` are validated here —
    an unknown value, or an address that doesn't parse (see :meth:`Address.parse`; this is
    how an incomplete address like bare ``manager`` — missing its project — is refused up
    front instead of silently writing to the wrong Maildir), is a :class:`UserError`, not
    a crash or a misdelivery.
    """
    if kind not in get_args(Kind):
        raise UserError(f'unknown kind {kind!r}; one of {", ".join(get_args(Kind))}')
    if priority not in get_args(Priority):
        raise UserError(f'unknown priority {priority!r}; one of {", ".join(get_args(Priority))}')
    try:
        Address.parse(to)
    except ValueError as error:
        raise UserError(str(error)) from error
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
