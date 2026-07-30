"""``ch session whoami`` — what this session is, on the evidence."""

from pathlib import Path

from chimera.context import seat
from chimera.identity import HUMAN, current_session


def whoami(cwd: Path) -> str:
    """Describe who is running this: the proven session, or a human.

    Answers the question an agent asks when it isn't sure — "am I addressed, and as
    what?" — with the same evidence the fence and mail use, so a session can never be
    told one thing here and treated as another elsewhere. The ``seat`` line is shown
    alongside precisely because the two differ: it is where the session is standing,
    which entitles it to nothing.
    """
    session = current_session(cwd)
    if session is None:
        return f'{HUMAN}\nseat: {seat(cwd)}\n(no session of a registered harness is running this)'
    lines = [session.address or f'unaddressed ({session.native_id[:8]})']
    lines.append(f'seat: {seat(cwd)}')
    lines.append(f'session: {session.platform} {session.native_id}')
    lines.append(
        f'status: {session.status}' + ('' if session.addressable else ' (not a conversation)')
    )
    if session.address is None:
        lines.append(
            'this session holds no address: no mail routes to it, and it fills no board slot'
        )
    return '\n'.join(lines)
