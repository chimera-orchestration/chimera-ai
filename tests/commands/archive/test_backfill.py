import json
from datetime import datetime, timezone
from pathlib import Path

from testfixtures import Replacer, TempDir, compare, like

from chimera.agents.claude import Claude
from chimera.archive import Archive, ArchiveSession
from chimera.commands.archive.backfill import Backfilled, backfill
from chimera.commands.hook.capture import session_start
from tests.cli import Command, action_logs

UUID = 'aaaaaaaa-0000-4000-8000-000000000001'
OTHER = 'aaaaaaaa-0000-4000-8000-000000000002'
THIRD = 'aaaaaaaa-0000-4000-8000-000000000003'
FOURTH = 'aaaaaaaa-0000-4000-8000-000000000004'


def _archived(ws: Path) -> list[ArchiveSession]:
    with Archive.open(ws / 'state' / 'archive.db') as a:
        return a.sessions()


def _entry(cwd: Path | None, timestamp: str | None) -> dict[str, object]:
    return {'type': 'user', 'cwd': str(cwd) if cwd else None, 'timestamp': timestamp}


def _write(store: Path, uuid: str, *lines: object) -> Path:
    transcript = store / 'some-session-dir' / f'{uuid}.jsonl'
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(
        '\n'.join(line if isinstance(line, str) else json.dumps(line) for line in lines)
    )
    return transcript


def test_goal_worktree_cwd_resolves_all_axes_and_the_timestamp_span(tmpdir: TempDir) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    tmpdir.dump('ws/proj/config.yaml', {'kind': 'project', 'repo': '/r'})
    worktree = tmpdir.path / 'ws' / 'proj' / 'worktrees' / 'g@agent'
    store = tmpdir.path / 'claude'
    transcript = _write(
        store,
        UUID,
        {'type': 'custom-title', 'cwd': None, 'timestamp': None},
        _entry(worktree, '2026-07-09T06:52:57.081Z'),
        _entry(worktree, '2026-07-09T06:50:00Z'),  # earliest, deliberately not first
        _entry(worktree, '2026-07-09T07:10:00Z'),
    )
    compare(backfill(store), expected=Backfilled(imported=1, present=0, outside=0, unplaced=0))
    compare(
        _archived(tmpdir.path / 'ws'),
        expected=[
            ArchiveSession(
                platform='claude',
                native_id=UUID,
                status='backfilled',
                started_at=datetime(2026, 7, 9, 6, 50, tzinfo=timezone.utc),
                ended_at=datetime(2026, 7, 9, 7, 10, tzinfo=timezone.utc),
                address='proj@g@agent',
                cwd=worktree,
                transcript=transcript,
                workspace='ws',
                project='proj',
                goal='g',
                actor='agent',
            )
        ],
    )


def test_swept_worktree_cwd_keeps_its_axes(tmpdir: TempDir) -> None:
    # the dominant historical case: the goal finished, its worktree (the cwd) is gone —
    # the dir shape still names the goal and a non-default actor
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    tmpdir.dump('ws/proj/config.yaml', {'kind': 'project', 'repo': '/r'})
    gone = tmpdir.path / 'ws' / 'proj' / 'worktrees' / 'g@reviewer' / 'subdir'
    store = tmpdir.path / 'claude'
    _write(store, UUID, _entry(gone, '2026-07-09T06:52:57.081Z'))
    compare(backfill(store), expected=Backfilled(imported=1, present=0, outside=0, unplaced=0))
    compare(
        _archived(tmpdir.path / 'ws'),
        expected=[
            like(
                ArchiveSession,
                address='proj@g@reviewer',
                project='proj',
                goal='g',
                actor='reviewer',
            )
        ],
    )


def test_a_bare_workspace_session_claims_nothing(tmpdir: TempDir) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace', 'captain': 'pegasus'})
    ws = tmpdir.path / 'ws'
    store = tmpdir.path / 'claude'
    transcript = _write(store, UUID, _entry(ws, '2026-07-09T06:52:57.081Z'))
    compare(backfill(store), expected=Backfilled(imported=1, present=0, outside=0, unplaced=0))
    compare(
        _archived(ws),
        expected=[
            ArchiveSession(
                platform='claude',
                native_id=UUID,
                status='backfilled',
                started_at=datetime(2026, 7, 9, 6, 52, 57, 81000, tzinfo=timezone.utc),
                ended_at=datetime(2026, 7, 9, 6, 52, 57, 81000, tzinfo=timezone.utc),
                cwd=ws,
                transcript=transcript,
                workspace='ws',
            )
        ],
    )


def test_a_project_dir_session_claims_nothing(tmpdir: TempDir) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    tmpdir.dump('ws/proj/config.yaml', {'kind': 'project', 'repo': '/r'})
    store = tmpdir.path / 'claude'
    _write(store, UUID, _entry(tmpdir.path / 'ws' / 'proj', '2026-07-09T06:52:57.081Z'))
    backfill(store)
    compare(
        _archived(tmpdir.path / 'ws'),
        expected=[
            like(
                ArchiveSession,
                project='proj',
                goal=None,
                actor=None,
            )
        ],
    )


def test_the_env_workspace_pin_never_places_a_historical_cwd(
    tmpdir: TempDir, replace: Replacer
) -> None:
    # the hook may trust $CHIMERA_WORKSPACE (its session's own env); a historical cwd is
    # placed by its path alone — the pinned workspace gets nothing from either transcript
    tmpdir.dump('home/config.yaml', {'kind': 'workspace'})
    tmpdir.dump('elsewhere/config.yaml', {'kind': 'workspace'})
    replace.in_environ('CHIMERA_WORKSPACE', str(tmpdir / 'home'))
    outside = tmpdir.path / 'outside'
    outside.mkdir()
    store = tmpdir.path / 'claude'
    _write(store, UUID, _entry(tmpdir.path / 'elsewhere', '2026-07-09T06:52:57.081Z'))
    _write(store, OTHER, _entry(outside, '2026-07-09T06:52:57.081Z'))
    compare(backfill(store), expected=Backfilled(imported=1, present=0, outside=1, unplaced=0))
    compare(_archived(tmpdir.path / 'elsewhere'), expected=[like(ArchiveSession, native_id=UUID)])
    assert not (tmpdir.path / 'home' / 'state').exists()


def test_cwd_outside_any_workspace_is_skipped(tmpdir: TempDir) -> None:
    elsewhere = tmpdir.path / 'elsewhere'
    elsewhere.mkdir()
    store = tmpdir.path / 'claude'
    _write(store, UUID, _entry(elsewhere, '2026-07-09T06:52:57.081Z'))
    compare(backfill(store), expected=Backfilled(imported=0, present=0, outside=1, unplaced=0))
    assert not (elsewhere / 'state').exists()


def test_timestamp_offsets_normalise_to_utc(tmpdir: TempDir) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    store = tmpdir.path / 'claude'
    _write(store, UUID, _entry(ws, '2026-07-09T08:00:00+01:00'))
    backfill(store)
    compare(
        _archived(ws),
        expected=[
            like(
                ArchiveSession,
                started_at=datetime(2026, 7, 9, 7, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 7, 9, 7, 0, tzinfo=timezone.utc),
            )
        ],
    )


def test_rerun_leaves_backfilled_rows_untouched(tmpdir: TempDir) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace', 'captain': 'pegasus'})
    ws = tmpdir.path / 'ws'
    store = tmpdir.path / 'claude'
    _write(store, UUID, _entry(ws, '2026-07-09T06:52:57.081Z'))
    compare(backfill(store), expected=Backfilled(imported=1, present=0, outside=0, unplaced=0))
    before = _archived(ws)
    compare(backfill(store), expected=Backfilled(imported=0, present=1, outside=0, unplaced=0))
    compare(_archived(ws), expected=before)


def test_hook_recorded_session_is_left_untouched(tmpdir: TempDir, replace: Replacer) -> None:
    # firing a start hook asks who else is working there, and so the registry
    replace.on_class(Claude.live, lambda self, cwd=None: [])
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace', 'captain': 'pegasus'})
    ws = tmpdir.path / 'ws'
    session_start(
        Claude(),
        {'cwd': str(ws), 'session_id': UUID, 'transcript_path': f'/t/{UUID}.jsonl'},
        {},
    )
    before = _archived(ws)
    store = tmpdir.path / 'claude'
    _write(store, UUID, _entry(ws, '2020-01-01T00:00:00Z'))
    compare(backfill(store), expected=Backfilled(imported=0, present=1, outside=0, unplaced=0))
    compare(_archived(ws), expected=before)


def test_unplaceable_transcripts_are_counted_not_imported(tmpdir: TempDir) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace', 'captain': 'pegasus'})
    ws = tmpdir.path / 'ws'
    store = tmpdir.path / 'claude'
    _write(  # a cwd but no timezone-aware timestamp, amid tolerated junk
        store,
        UUID,
        'not json',
        json.dumps([1]),
        _entry(ws, '2026-07-09T06:52:57'),  # naive
        {'type': 'x', 'cwd': str(ws), 'timestamp': 123},
        _entry(ws, 'not-a-time'),
    )
    _write(store, OTHER, _entry(None, '2026-07-09T06:52:57.081Z'))  # timestamps but no cwd
    (store / 'some-session-dir' / f'{THIRD}.jsonl').mkdir()  # unreadable: a directory
    (store / 'some-session-dir' / f'{FOURTH}.jsonl').write_bytes(b'\xff\xfe junk')  # undecodable
    compare(backfill(store), expected=Backfilled(imported=0, present=0, outside=0, unplaced=4))
    assert not (ws / 'state').exists()


def test_cli(tmpdir: TempDir, command: Command) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace', 'captain': 'pegasus'})
    ws = tmpdir.path / 'ws'
    store = tmpdir.path / 'claude'
    _write(store, UUID, _entry(ws, '2026-07-09T06:52:57.081Z'))
    start, end = action_logs(
        'archive backfill', 'chimera.commands.archive.backfill.backfill', {'projects': str(store)}
    )
    command.run('archive', 'backfill', '--projects', str(store)).check(
        output='Imported 1 (already archived: 0, outside any workspace: 0, unplaceable: 0)\n',
        logging=[
            start,
            {
                'level': 'INFO',
                'message': 'archive backfill: scanned',
                'imported': 1,
                'present': 0,
                'outside': 0,
                'unplaced': 0,
            },
            end,
        ],
    )
    assert _archived(ws)[0].native_id == UUID
