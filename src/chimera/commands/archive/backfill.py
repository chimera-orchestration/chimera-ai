"""Archive backfill: import claude's pre-hook transcripts into the session archive.

The SessionStart/SessionEnd hooks only capture sessions run since they were installed;
everything earlier exists solely as a ``<uuid>.jsonl`` transcript under
``~/.claude/projects/*/`` — the filename is the session UUID, exactly the archive's
``native_id`` for platform ``claude``. Each transcript's entries carry the session's cwd
and per-entry timestamps, so a historical session is reconstructed: axes from the cwd's
*path* (see :func:`_axes` for why the hook's live resolver would misplace it),
``started_at``/``ended_at`` from the earliest/latest entry timestamps, and status
``backfilled`` marking the row as reconstructed rather than hook-recorded. ``manager``
is ``chimera`` for a goal-worktree session (only chimera launches those) and ``none``
elsewhere — a historical chat's launcher is unknowable from its transcript. A session
the archive already knows is left untouched — the insert itself skips a known identity,
so a re-run (or a hook racing the scan) never clobbers hook-recorded or previously
backfilled rows. Undoing a backfill is deleting the ``backfilled`` rows.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from chimera.agent_env import ROLE_MANAGER
from chimera.archive import Archive, Session
from chimera.commands.hook.capture import archive
from chimera.config import (
    NotInWorkspaceError,
    ProjectConfig,
    find_workspace,
    load_config,
    workspace_config,
)
from chimera.worktrees import SEP, session_name

CLAUDE_PROJECTS = Path.home() / '.claude' / 'projects'
BACKFILLED = 'backfilled'

_Axes = tuple[Path, str | None, str | None, str | None]


@dataclass(frozen=True)
class Backfilled:
    """What a backfill scan did: sessions imported, and why the rest were skipped."""

    imported: int
    present: int
    outside: int
    unplaced: int


def backfill(projects: Path) -> Backfilled:
    """Import every transcript under ``projects/*/*.jsonl`` its workspace's archive lacks.

    A transcript already archived (hook-recorded or previously backfilled) is left
    untouched (``present``); one whose cwd falls outside any workspace — by the cwd's own
    path, never today's ``$CHIMERA_WORKSPACE`` — is skipped (``outside``); one that never
    yields a cwd and a timezone-aware timestamp can't be placed (``unplaced``).
    """
    imported = present = outside = unplaced = 0
    archives: dict[Path, Archive] = {}
    try:
        for transcript in sorted(projects.glob('*/*.jsonl')):
            placed = _placement(transcript)
            if placed is None:
                unplaced += 1
                continue
            cwd, started, ended = placed
            resolved = _axes(cwd)
            if resolved is None:
                outside += 1
                continue
            workspace, project, goal, actor = resolved
            if workspace not in archives:
                archives[workspace] = archive(workspace)
            session = Session(
                platform='claude',
                native_id=transcript.stem,
                status=BACKFILLED,
                started_at=started,
                ended_at=ended,
                manager='chimera' if goal is not None else 'none',
                name=_name(workspace, project, goal, actor),
                cwd=cwd,
                transcript=transcript,
                workspace=workspace.name,
                project=project,
                goal=goal,
                actor=actor,
            )
            if archives[workspace].record_session_if_absent(session):
                imported += 1
            else:
                present += 1
    finally:
        for store in archives.values():
            store.close()
    logger.bind(imported=imported, present=present, outside=outside, unplaced=unplaced).info(
        'archive backfill: scanned'
    )
    return Backfilled(imported=imported, present=present, outside=outside, unplaced=unplaced)


def _axes(cwd: Path) -> _Axes | None:
    """``(workspace, project, goal, actor)`` for a historical cwd, from the path alone.

    Deliberately *not* the hook's resolver (``capture._axes``): that places a live
    session, whose environment and worktrees are its own. A historical cwd needs the
    opposite — today's ``$CHIMERA_WORKSPACE`` says nothing about where an old session
    ran, a finished goal's worktree (often the cwd itself) no longer exists, and the
    live resolver's repo-identity fallback runs git *in the cwd*, which may be a deleted
    directory. So: walk the path up for the workspace and project ``config.yaml``
    markers, and read goal/actor from the ``worktrees/<goal>@<actor>`` dir shape —
    none of which needs the cwd to still exist.
    """
    try:
        workspace = find_workspace(cwd)
    except NotInWorkspaceError:
        return None
    project: Path | None = None
    for directory in (cwd, *cwd.parents):
        if directory == workspace:
            break
        if isinstance(load_config(directory), ProjectConfig):
            project = directory
            break
    if project is None:
        return workspace, None, None, None
    relative = cwd.relative_to(project).parts
    if len(relative) >= 2 and relative[0] == 'worktrees' and SEP in relative[1]:
        goal, actor = relative[1].split(SEP, 1)
        return workspace, project.name, goal, actor
    return workspace, project.name, None, None


def _name(workspace: Path, project: str | None, goal: str | None, actor: str | None) -> str:
    """The address the session would have had — ``caller``'s cwd inference, from the axes."""
    if project is None:
        return workspace_config(workspace).captain.name
    if goal is None or actor is None:
        return f'{project}{SEP}{ROLE_MANAGER}'
    return session_name(project, goal, actor)


def _placement(transcript: Path) -> tuple[Path, datetime, datetime] | None:
    """The transcript's cwd and its earliest/latest entry timestamps.

    A transcript's early entries (titles, mode changes) carry neither field; the first
    entry with a cwd names the session's, and every timezone-aware timestamp widens the
    span. Junk lines are tolerated; an unreadable or undecodable file, or one that never
    yields both a cwd and a timestamp, is ``None``.
    """
    cwd: Path | None = None
    first: datetime | None = None
    last: datetime | None = None
    try:
        with transcript.open() as file:
            for line in file:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if cwd is None and entry.get('cwd'):
                    cwd = Path(str(entry['cwd']))
                at = _timestamp(entry)
                if at is not None:
                    first = at if first is None or at < first else first
                    last = at if last is None or at > last else last
    except (OSError, UnicodeDecodeError):
        return None
    if cwd is None or first is None or last is None:
        return None
    return cwd, first, last


def _timestamp(entry: dict[str, object]) -> datetime | None:
    """The entry's timestamp in UTC — only a timezone-aware one is safe to order by."""
    raw = entry.get('timestamp')
    if not isinstance(raw, str):
        return None
    try:
        at = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return at.astimezone(timezone.utc) if at.tzinfo is not None else None
