"""``ch session show`` — the archive's whole record of one session."""

from pathlib import Path

from chimera.archive import archive
from chimera.config import UserError
from chimera.context import resolve_workspace


def show(cwd: Path, session: str, platform: str = 'claude') -> str:
    """Everything recorded about ``session``: its row, then its timeline.

    ``session`` is a native id or its leading block, so the short form a listing shows
    can be pasted straight back. The timeline is what makes a session's life legible —
    a resume is one row with accumulating events, and a bridge is a *new* row whose
    ``branched`` event is the only sign of where it came from.
    """
    workspace = resolve_workspace(cwd)
    with archive(workspace) as store:
        matches = [s for s in store.sessions(platform=platform) if s.native_id.startswith(session)]
        if not matches:
            raise UserError(f'no {platform} session here starting {session!r}')
        if len(matches) > 1:
            ids = ', '.join(s.native_id for s in matches)
            raise UserError(f'{session!r} matches several sessions: {ids}')
        found = matches[0]
        events = store.events(platform=platform, native_id=found.native_id)
    lines = [
        f'{found.address or "(unaddressed)"}  {found.platform} {found.native_id}',
        f'status: {found.status}' + ('' if found.addressable else '  (not a conversation)'),
        f'where: {found.cwd}',
        f'axes: {found.workspace or "-"} / {found.project or "-"} / {found.goal or "-"}'
        f' / {found.actor or "-"}',
    ]
    if found.model:
        lines.append(f'model: {found.model}')
    if found.harness_version:
        lines.append(f'harness: {found.harness_version}')
    if found.transcript is not None:
        gone = ' (gone — this session can no longer be resumed)' if found.transcript_missing else ''
        lines.append(f'transcript: {found.transcript}{gone}')
    lines.append('timeline:')
    lines += [
        f'  {event.at.isoformat()}  {event.kind}' + (f'  {event.detail}' if event.detail else '')
        for event in events
    ]
    return '\n'.join(lines)
