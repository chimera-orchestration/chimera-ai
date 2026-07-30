import sqlite3
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Thread

import pytest
from testfixtures import ShouldRaise, TempDir, compare

from chimera.archive import Archive, ArchiveSession, Event, migrate, needs_migration
from chimera.config import UserError

NOON = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def make_session(
    native_id: str,
    *,
    platform: str = 'claude',
    status: str = 'running',
    started_at: datetime = NOON,
    ended_at: datetime | None = None,
    model: str | None = None,
    address: str | None = None,
    addressable: bool = True,
    harness_version: str | None = None,
    cwd: Path | None = None,
    transcript: Path | None = None,
    workspace: str | None = None,
    project: str | None = None,
    goal: str | None = None,
    actor: str | None = None,
) -> ArchiveSession:
    return ArchiveSession(
        native_id=native_id,
        platform=platform,
        status=status,
        started_at=started_at,
        ended_at=ended_at,
        model=model,
        address=address,
        addressable=addressable,
        harness_version=harness_version,
        cwd=cwd,
        transcript=transcript,
        workspace=workspace,
        project=project,
        goal=goal,
        actor=actor,
    )


@pytest.fixture()
def db_path(tmpdir: TempDir) -> Path:
    return tmpdir.path / 'archive.db'


@pytest.fixture()
def archive(db_path: Path) -> Iterator[Archive]:
    with Archive.open(db_path) as a:
        yield a


def test_open_creates_the_database_file(db_path: Path) -> None:
    assert not db_path.exists()
    with Archive.open(db_path):
        pass
    assert db_path.exists()


def test_open_creates_missing_parent_directories(tmpdir: TempDir) -> None:
    nested = tmpdir.path / 'a' / 'b' / 'archive.db'
    with Archive.open(nested):
        pass
    assert nested.exists()


def test_open_enables_wal_so_readers_and_writers_dont_block(db_path: Path) -> None:
    with Archive.open(db_path):
        pass
    fresh = sqlite3.connect(db_path)
    assert fresh.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    fresh.close()


def test_a_recorded_session_comes_back_exactly(archive: Archive) -> None:
    session = make_session(
        '00893aaf-19fa-41d2-8238-13269b9b3ca0',
        model='claude-opus-4-8',
        address='chimera@logs-and-sessions@agent',
        harness_version='claude-code_2-1-220_agent',
        project='chimera',
        goal='logs-and-sessions',
        actor='agent',
        cwd=Path('/work/chimera/logs-and-sessions@agent'),
        workspace='lycia',
    )
    archive.record_session(session)
    assert archive.session('claude', '00893aaf-19fa-41d2-8238-13269b9b3ca0') == session


def test_an_unknown_session_is_none(archive: Archive) -> None:
    assert archive.session('claude', 'never-seen') is None


def test_the_same_native_id_on_two_platforms_are_distinct_sessions(archive: Archive) -> None:
    archive.record_session(make_session('shared', platform='claude'))
    archive.record_session(make_session('shared', platform='codex'))
    assert archive.session('claude', 'shared') is not None
    assert archive.session('codex', 'shared') is not None
    assert len(archive.sessions()) == 2  # identity is (platform, native_id), not native_id alone


def test_recording_the_same_identity_again_updates_in_place(archive: Archive) -> None:
    archive.record_session(make_session('s1', status='running'))
    archive.record_session(make_session('s1', status='compacting', model='haiku'))
    stored = archive.session('claude', 's1')
    assert stored is not None
    assert stored.status == 'compacting'
    assert stored.model == 'haiku'
    assert archive.sessions() == [stored]  # updated, not duplicated


def test_rerecording_preserves_the_original_start_time(archive: Archive) -> None:
    archive.record_session(make_session('s1', status='startup', started_at=NOON))
    archive.record_session(
        make_session('s1', status='resume', started_at=NOON + timedelta(hours=8))
    )
    stored = archive.session('claude', 's1')
    assert stored is not None
    assert stored.started_at == NOON  # first write wins — a resume is not a new run
    assert stored.status == 'resume'


def test_rerecording_reopens_an_ended_session(archive: Archive) -> None:
    archive.record_session(make_session('s1', status='startup'))
    archive.end_session('claude', 's1', at=NOON + timedelta(hours=1), status='other')
    archive.record_session(make_session('s1', status='resume', ended_at=None))
    stored = archive.session('claude', 's1')
    assert stored is not None
    assert stored.ended_at is None  # resumed — no longer ended
    assert stored.status == 'resume'


def test_rerecording_never_erases_a_known_address(archive: Archive) -> None:
    archive.record_session(make_session('s1', status='startup', address='p@g@agent'))
    # a raw `claude --resume` or the `claude agents` browser stamps no role
    archive.record_session(make_session('s1', status='resume', address=None))
    stored = archive.session('claude', 's1')
    assert stored is not None
    assert stored.address == 'p@g@agent'  # sticky — a resume carries no fresh evidence
    assert stored.status == 'resume'  # everything else still takes the new value


def test_rerecording_still_takes_a_newly_claimed_address(archive: Archive) -> None:
    archive.record_session(make_session('s1', status='startup', address='p@g@agent'))
    archive.record_session(make_session('s1', status='resume', address='p@g@reviewer'))
    stored = archive.session('claude', 's1')
    assert stored is not None
    assert stored.address == 'p@g@reviewer'  # only erasure is refused, not a real change


def test_record_if_absent_never_clobbers_an_existing_row(archive: Archive) -> None:
    first = make_session('s1', status='running')
    assert archive.record_session_if_absent(first)
    assert not archive.record_session_if_absent(make_session('s1', status='backfilled'))
    assert archive.session('claude', 's1') == first
    assert archive.sessions() == [first]  # skipped, not duplicated


def test_ending_a_session_stamps_when_and_how_it_finished(archive: Archive) -> None:
    archive.record_session(make_session('s1', status='running'))
    archive.end_session('claude', 's1', at=NOON + timedelta(hours=2), status='done')
    stored = archive.session('claude', 's1')
    assert stored is not None
    assert stored.status == 'done'
    assert stored.ended_at == NOON + timedelta(hours=2)


# --- the four questions: which chat, which goal, which agents, when ----------------


def test_sessions_for_a_goal_lists_every_chat_that_worked_it(archive: Archive) -> None:
    archive.record_session(
        make_session(
            'a', address='chimera@logs@agent', project='chimera', goal='logs', actor='agent'
        )
    )
    archive.record_session(make_session('b', project='chimera', goal='logs', actor='human'))
    archive.record_session(make_session('c', project='chimera', goal='docs', actor='agent'))
    on_logs = archive.sessions(project='chimera', goal='logs')
    assert [s.native_id for s in on_logs] == ['a', 'b']


def test_actors_for_a_goal_answers_which_agents_worked_on_it(archive: Archive) -> None:
    archive.record_session(
        make_session(
            'a', address='chimera@logs@agent', project='chimera', goal='logs', actor='agent'
        )
    )
    archive.record_session(make_session('b', project='chimera', goal='logs', actor='human'))
    archive.record_session(
        make_session(
            'c', address='chimera@logs@agent', project='chimera', goal='logs', actor='agent'
        )
    )
    assert archive.actors_for_goal('chimera', 'logs') == ['agent', 'human']


def test_sessions_can_be_narrowed_by_platform(archive: Archive) -> None:
    archive.record_session(make_session('a', platform='claude'))
    archive.record_session(make_session('b', platform='claude'))
    archive.record_session(make_session('c', platform='codex'))
    assert [s.native_id for s in archive.sessions(platform='claude')] == ['a', 'b']
    assert [s.native_id for s in archive.sessions(platform='codex')] == ['c']


def test_sessions_can_be_narrowed_by_workspace_and_actor(archive: Archive) -> None:
    archive.record_session(make_session('a', workspace='lycia', actor='agent'))
    archive.record_session(make_session('b', workspace='lycia', actor='human'))
    archive.record_session(make_session('c', workspace='other', actor='agent'))
    assert [s.native_id for s in archive.sessions(workspace='lycia')] == ['a', 'b']
    assert [s.native_id for s in archive.sessions(workspace='lycia', actor='agent')] == ['a']


def test_active_sessions_are_the_ones_not_yet_ended(archive: Archive) -> None:
    archive.record_session(make_session('live', status='running'))
    archive.record_session(make_session('done', status='done', ended_at=NOON + timedelta(hours=1)))
    assert [s.native_id for s in archive.sessions(active=True)] == ['live']
    assert [s.native_id for s in archive.sessions(active=False)] == ['done']


def test_sessions_come_back_oldest_first(archive: Archive) -> None:
    archive.record_session(make_session('second', started_at=NOON + timedelta(hours=1)))
    archive.record_session(make_session('first', started_at=NOON))
    assert [s.native_id for s in archive.sessions()] == ['first', 'second']


# --- address → live session (what comms routes on) ---------------------------------


def test_live_session_for_returns_the_current_session_at_an_address(archive: Archive) -> None:
    archive.record_session(
        make_session(
            'now', address='chimera@logs@agent', project='chimera', goal='logs', actor='agent'
        )
    )
    live = archive.live_session_for('chimera@logs@agent')
    assert live is not None
    assert live.native_id == 'now'


def test_live_session_for_prefers_the_newest_and_ignores_ended(archive: Archive) -> None:
    archive.record_session(
        make_session(
            'old',
            address='chimera@logs@agent',
            project='chimera',
            goal='logs',
            actor='agent',
            started_at=NOON,
            ended_at=NOON + timedelta(hours=1),
        )
    )
    archive.record_session(
        make_session(
            'new',
            address='chimera@logs@agent',
            project='chimera',
            goal='logs',
            actor='agent',
            started_at=NOON + timedelta(hours=2),
        )
    )
    live = archive.live_session_for('chimera@logs@agent')
    assert live is not None
    assert live.native_id == 'new'


def test_live_session_for_is_none_when_the_address_has_no_live_session(archive: Archive) -> None:
    archive.record_session(
        make_session(
            'gone',
            project='chimera',
            goal='logs',
            actor='agent',
            ended_at=NOON + timedelta(hours=1),
        )
    )
    assert archive.live_session_for('chimera@logs@agent') is None


def test_latest_session_for_finds_the_newest_even_when_ended(archive: Archive) -> None:
    archive.record_session(
        make_session(
            'old',
            address='chimera@logs@agent',
            project='chimera',
            goal='logs',
            actor='agent',
            started_at=NOON,
        )
    )
    archive.record_session(
        make_session(
            'new',
            address='chimera@logs@agent',
            project='chimera',
            goal='logs',
            actor='agent',
            started_at=NOON + timedelta(hours=2),
            ended_at=NOON + timedelta(hours=3),  # dead — resuming is how it's revived
        )
    )
    latest = archive.latest_session_for('chimera', 'logs', 'agent')
    assert latest is not None
    assert latest.native_id == 'new'


def test_latest_session_for_survives_a_registry_rename(archive: Archive) -> None:
    # a UI rename mutates the registry name; the archive row still answers the address
    archive.record_session(
        make_session(
            's1', address='free-form rename', project='chimera', goal='logs', actor='agent'
        )
    )
    latest = archive.latest_session_for('chimera', 'logs', 'agent')
    assert latest is not None
    assert latest.native_id == 's1'


def test_latest_session_for_prefers_the_last_active_over_the_last_created(
    archive: Archive,
) -> None:
    # started_at is first-write-wins, so creation order lies about activity: thread A,
    # /clear -> thread B (abandoned), A resumed later — resume must pick A, not B
    archive.record_session(
        make_session(
            'a',
            address='chimera@logs@agent',
            project='chimera',
            goal='logs',
            actor='agent',
            started_at=NOON,
        )
    )
    archive.record_session(
        make_session(
            'b',
            project='chimera',
            goal='logs',
            actor='agent',
            started_at=NOON + timedelta(hours=1),
        )
    )
    archive.record_event(
        Event(at=NOON + timedelta(hours=2), kind='resume', platform='claude', native_id='a')
    )
    latest = archive.latest_session_for('chimera', 'logs', 'agent')
    assert latest is not None
    assert latest.native_id == 'a'


def test_latest_session_for_narrows_by_platform(archive: Archive) -> None:
    archive.record_session(
        make_session('c', platform='codex', project='chimera', goal='logs', actor='agent')
    )
    assert archive.latest_session_for('chimera', 'logs', 'agent', platform='claude') is None
    latest = archive.latest_session_for('chimera', 'logs', 'agent', platform='codex')
    assert latest is not None
    assert latest.native_id == 'c'


def test_latest_session_for_is_none_for_an_unseen_address(archive: Archive) -> None:
    archive.record_session(make_session('unaddressed', project='chimera', goal='logs', actor=None))
    assert archive.latest_session_for('chimera', 'logs', 'agent') is None


def test_latest_session_for_a_manager_address_wants_goal_and_actor_null(
    archive: Archive,
) -> None:
    # a manager address (project set, no goal/actor) must not match a goal actor row —
    # None here means IS NULL, not "unconstrained" (unlike sessions())
    archive.record_session(make_session('agent-row', project='chimera', goal='g', actor='agent'))
    archive.record_session(make_session('mgr-row', project='chimera'))
    latest = archive.latest_session_for('chimera')
    assert latest is not None
    assert latest.native_id == 'mgr-row'


def test_latest_session_for_the_captain_address_wants_every_axis_null(archive: Archive) -> None:
    archive.record_session(make_session('mgr-row', project='chimera'))
    archive.record_session(make_session('captain-row'))
    latest = archive.latest_session_for(None)
    assert latest is not None
    assert latest.native_id == 'captain-row'


def test_latest_session_for_narrows_by_address(archive: Archive) -> None:
    # project/goal/actor alone can't pin the captain/manager address (every axis-less row
    # matches); the address itself does
    archive.record_session(make_session('other', started_at=NOON + timedelta(hours=1)))
    archive.record_session(make_session('pegasus', address='pegasus', started_at=NOON))
    latest = archive.latest_session_for(None, address='pegasus')
    assert latest is not None
    assert latest.native_id == 'pegasus'


def test_recent_sessions_orders_by_last_active_newest_first(archive: Archive) -> None:
    archive.record_session(make_session('a', workspace='ws', started_at=NOON))
    archive.record_session(make_session('b', workspace='ws', started_at=NOON + timedelta(hours=1)))
    archive.record_event(
        Event(at=NOON + timedelta(hours=5), kind='resume', platform='claude', native_id='a')
    )
    assert [s.native_id for s in archive.recent_sessions('ws', limit=10)] == ['a', 'b']


def test_recent_sessions_scopes_to_the_workspace(archive: Archive) -> None:
    archive.record_session(make_session('a', workspace='ws'))
    archive.record_session(make_session('other', workspace='other-ws'))
    assert [s.native_id for s in archive.recent_sessions('ws', limit=10)] == ['a']


def test_recent_sessions_excludes_claimed_identities(archive: Archive) -> None:
    archive.record_session(make_session('a', workspace='ws', address='a', started_at=NOON))
    archive.record_session(
        make_session('b', workspace='ws', address='b', started_at=NOON + timedelta(hours=1))
    )
    recent = archive.recent_sessions('ws', exclude=['b'], limit=10)
    assert [s.native_id for s in recent] == ['a']


def test_recent_sessions_excludes_every_row_sharing_a_claimed_name(archive: Archive) -> None:
    # an old resume of a claimed slot shares its name but not its native_id — exclude by
    # name, not just the one (platform, native_id) the slot picked as its current occupant
    archive.record_session(
        make_session('old-resume', workspace='ws', address='pegasus', started_at=NOON)
    )
    archive.record_session(
        make_session(
            'current', workspace='ws', address='pegasus', started_at=NOON + timedelta(hours=1)
        )
    )
    assert archive.recent_sessions('ws', exclude=['pegasus'], limit=10) == []


def test_recent_sessions_keeps_unnamed_sessions_when_excluding(archive: Archive) -> None:
    # NULL NOT IN (...) is NULL (falsy) in SQL, not true — an unnamed row must stay eligible
    archive.record_session(make_session('unnamed', workspace='ws', started_at=NOON))
    recent = archive.recent_sessions('ws', exclude=['someone-else'], limit=10)
    assert [s.native_id for s in recent] == ['unnamed']


def test_recent_sessions_respects_the_limit(archive: Archive) -> None:
    for i in range(3):
        archive.record_session(
            make_session(str(i), workspace='ws', started_at=NOON + timedelta(hours=i))
        )
    assert len(archive.recent_sessions('ws', limit=2)) == 2


# --- events: the timeline that stitches logs to sessions ---------------------------


def test_events_for_a_session_form_a_timeline(archive: Archive) -> None:
    archive.record_session(make_session('s1'))
    archive.record_event(Event(at=NOON, kind='started', platform='claude', native_id='s1'))
    archive.record_event(
        Event(
            at=NOON + timedelta(minutes=5),
            kind='committed',
            detail='abc123',
            platform='claude',
            native_id='s1',
        )
    )
    timeline = archive.events(platform='claude', native_id='s1')
    assert [(e.kind, e.detail) for e in timeline] == [('started', None), ('committed', 'abc123')]


def test_events_without_a_session_are_kept_too(archive: Archive) -> None:
    archive.record_event(Event(at=NOON, kind='doctor-run', detail='all checks passed'))
    everything = archive.events()
    assert [e.kind for e in everything] == ['doctor-run']
    assert everything[0].native_id is None


class TestTranscriptMissing:
    # what turns `ch agent resume` into claude's raw "No conversation found" traceback

    def test_a_present_transcript_is_not_missing(self, tmpdir: TempDir) -> None:
        assert not make_session('a', transcript=tmpdir.write('t.jsonl', '')).transcript_missing

    def test_a_pruned_transcript_is_missing(self, tmpdir: TempDir) -> None:
        assert make_session('a', transcript=tmpdir.path / 'gone.jsonl').transcript_missing

    def test_a_session_with_no_transcript_at_all_is_not_missing(self) -> None:
        # nothing was ever recorded to lose — distinct from a path that has vanished
        assert not make_session('a', transcript=None).transcript_missing


# --- concurrency: many agents writing the same archive at once ---------------------


def test_many_agents_write_the_same_archive_concurrently(db_path: Path) -> None:
    Archive.open(db_path).close()  # create the file up front; threads only write
    count = 12
    ready = Barrier(count)

    def agent(n: int) -> None:
        ready.wait()  # line everyone up so the writes genuinely overlap
        with Archive.open(db_path) as own:
            own.record_session(make_session(f'agent-{n:02d}', goal='shared', actor=f'a{n:02d}'))

    threads = [Thread(target=agent, args=(n,)) for n in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    with Archive.open(db_path) as archive:
        assert len(archive.sessions(goal='shared')) == count  # every write landed, none lost


# --- migration off the pre-trim schema ----------------------------------------------

LEGACY_SCHEMA = """
CREATE TABLE sessions (
    platform      TEXT NOT NULL,
    native_id     TEXT NOT NULL,
    status        TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    manager       TEXT NOT NULL DEFAULT 'none',
    model         TEXT,
    name          TEXT,
    ended_at      TEXT,
    cwd           TEXT,
    transcript    TEXT,
    summary       TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd      REAL,
    workspace     TEXT,
    project       TEXT,
    goal          TEXT,
    actor         TEXT,
    PRIMARY KEY (platform, native_id)
);
CREATE INDEX sessions_by_manager ON sessions(manager);
CREATE TABLE events (
    at          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    detail      TEXT,
    platform    TEXT,
    native_id   TEXT,
    -- the cascade the rebuild must not trip: verbatim from a real pre-trim archive
    FOREIGN KEY (platform, native_id) REFERENCES sessions(platform, native_id) ON DELETE CASCADE
);
CREATE VIRTUAL TABLE sessions_fts USING fts5(
    name, goal, project, summary, content='sessions', content_rowid='rowid'
);
CREATE TRIGGER sessions_ai AFTER INSERT ON sessions BEGIN
    INSERT INTO sessions_fts(rowid, name, goal, project, summary)
    VALUES (new.rowid, new.name, new.goal, new.project, new.summary);
END;
CREATE TRIGGER sessions_ad AFTER DELETE ON sessions BEGIN
    INSERT INTO sessions_fts(sessions_fts, rowid, name, goal, project, summary)
    VALUES ('delete', old.rowid, old.name, old.goal, old.project, old.summary);
END;
CREATE TRIGGER sessions_au AFTER UPDATE ON sessions BEGIN
    INSERT INTO sessions_fts(sessions_fts, rowid, name, goal, project, summary)
    VALUES ('delete', old.rowid, old.name, old.goal, old.project, old.summary);
    INSERT INTO sessions_fts(rowid, name, goal, project, summary)
    VALUES (new.rowid, new.name, new.goal, new.project, new.summary);
END;
"""

_LEGACY_ROW = (
    'INSERT INTO sessions (platform, native_id, status, started_at, manager, name, '
    'summary, cwd, workspace, project, goal, actor) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
)


def legacy_archive(path: Path, rows: Sequence[tuple[object, ...]] = ()) -> None:
    """A database on the pre-trim schema, carrying ``rows`` of legacy sessions."""
    connection = sqlite3.connect(path)
    connection.execute('PRAGMA foreign_keys=ON')
    connection.executescript(LEGACY_SCHEMA)
    for row in rows:
        connection.execute(_LEGACY_ROW, row)
    connection.commit()
    connection.close()


def legacy_row(
    native_id: str,
    *,
    manager: str = 'none',
    name: str | None = None,
    project: str | None = None,
    goal: str | None = None,
    actor: str | None = None,
) -> tuple[object, ...]:
    return (
        'claude',
        native_id,
        'running',
        NOON.isoformat(),
        manager,
        name,
        'a summary',
        '/work',
        'lycia',
        project,
        goal,
        actor,
    )


class TestMigration:
    def test_a_missing_database_needs_nothing(self, db_path: Path) -> None:
        assert not needs_migration(db_path)

    def test_a_current_database_needs_nothing(self, db_path: Path) -> None:
        Archive.open(db_path).close()
        assert not needs_migration(db_path)

    def test_a_legacy_database_is_recognised(self, db_path: Path) -> None:
        legacy_archive(db_path)
        assert needs_migration(db_path)

    def test_migrating_is_idempotent(self, db_path: Path) -> None:
        legacy_archive(db_path, [legacy_row('a', manager='chimera', name='p@g@agent')])
        compare(migrate(db_path), expected=1)
        assert not needs_migration(db_path)
        compare(migrate(db_path), expected=0)  # nothing left to do

    def test_the_sessions_survive_with_their_axes(self, db_path: Path) -> None:
        legacy_archive(
            db_path,
            [
                legacy_row(
                    'a', manager='chimera', name='p@g@agent', project='p', goal='g', actor='agent'
                )
            ],
        )
        migrate(db_path)
        with Archive.open(db_path) as store:
            [session] = store.sessions()
        compare(
            session,
            expected=ArchiveSession(
                platform='claude',
                native_id='a',
                status='running',
                started_at=NOON,
                address='p@g@agent',
                cwd=Path('/work'),
                workspace='lycia',
                project='p',
                goal='g',
                actor='agent',
            ),
        )

    def test_a_launcher_stamped_claim_survives(self, db_path: Path) -> None:
        # manager='chimera' is the old record of "a launcher stamped this" — the very
        # evidence the address rule now demands
        legacy_archive(db_path, [legacy_row('a', manager='chimera', name='@@captain')])
        migrate(db_path)
        with Archive.open(db_path) as store:
            [session] = store.sessions()
        compare(session.address, expected='@@captain')

    def test_a_goal_worktree_claim_survives(self, db_path: Path) -> None:
        # the axes name an actor, so the address followed the worktree, not a guess
        legacy_archive(
            db_path, [legacy_row('a', name='p@g@agent', project='p', goal='g', actor='agent')]
        )
        migrate(db_path)
        with Archive.open(db_path) as store:
            [session] = store.sessions()
        compare(session.address, expected='p@g@agent')

    def test_a_geography_only_claim_is_dropped(self, db_path: Path) -> None:
        # a raw session in a project dir was archived as that project's manager purely
        # because of where it sat — the claim the address rule exists to refuse
        legacy_archive(db_path, [legacy_row('a', name='p@@manager', project='p')])
        migrate(db_path)
        with Archive.open(db_path) as store:
            [session] = store.sessions()
        assert session.address is None

    def test_the_events_are_untouched(self, db_path: Path) -> None:
        legacy_archive(db_path, [legacy_row('a')])
        connection = sqlite3.connect(db_path)
        connection.execute(
            'INSERT INTO events (at, kind, platform, native_id) VALUES (?, ?, ?, ?)',
            (NOON.isoformat(), 'startup', 'claude', 'a'),
        )
        connection.commit()
        connection.close()
        migrate(db_path)
        with Archive.open(db_path) as store:
            compare([e.kind for e in store.events()], expected=['startup'])

    def test_the_search_machinery_is_gone(self, db_path: Path) -> None:
        legacy_archive(db_path)
        migrate(db_path)
        connection = sqlite3.connect(db_path)
        try:
            names = {row[0] for row in connection.execute('SELECT name FROM sqlite_master')}
        finally:
            connection.close()
        assert not {n for n in names if n.startswith('sessions_fts') or n.startswith('sessions_a')}


class TestResumableSessions:
    # the failure this whole design started from: resume handed claude an id it no longer
    # knew, and the user got a raw "No conversation found" traceback

    def _at(self, archive: Archive, native_id: str, transcript: Path, hours: int) -> None:
        archive.record_session(
            make_session(
                native_id,
                address='p@g@agent',
                project='p',
                goal='g',
                actor='agent',
                transcript=transcript,
                started_at=NOON + timedelta(hours=hours),
            )
        )

    def test_a_pruned_tip_is_skipped_for_the_newest_resolvable_one(
        self, archive: Archive, tmpdir: TempDir
    ) -> None:
        kept = tmpdir.write('kept.jsonl', '')
        self._at(archive, 'older', kept, 0)
        self._at(archive, 'newest', tmpdir.path / 'pruned.jsonl', 1)
        newest = archive.latest_session_for('p', 'g', 'agent')
        assert newest is not None
        compare(newest.native_id, expected='newest')  # a listing wants the truth…
        resumable = archive.latest_session_for('p', 'g', 'agent', resumable=True)
        assert resumable is not None
        compare(resumable.native_id, expected='older')  # …a resume wants what still works

    def test_nothing_resumable_is_none_rather_than_a_doomed_id(
        self, archive: Archive, tmpdir: TempDir
    ) -> None:
        self._at(archive, 'gone', tmpdir.path / 'pruned.jsonl', 0)
        assert archive.latest_session_for('p', 'g', 'agent', resumable=True) is None


def test_touch_keeps_a_long_running_session_ahead_of_a_fresher_corpse(
    archive: Archive, tmpdir: TempDir
) -> None:
    # lifecycle events only fire at start and end, so without a heartbeat a session
    # working for hours looks frozen at its start
    transcript = tmpdir.write('t.jsonl', '')
    archive.record_session(
        make_session('working', address='a', transcript=transcript, started_at=NOON)
    )
    archive.record_session(
        make_session(
            'corpse', address='a', transcript=transcript, started_at=NOON + timedelta(hours=5)
        )
    )
    latest = archive.latest_session_for(None, address='a')
    assert latest is not None
    compare(latest.native_id, expected='corpse')
    archive.touch('claude', 'working', NOON + timedelta(hours=6))
    latest = archive.latest_session_for(None, address='a')
    assert latest is not None
    compare(latest.native_id, expected='working')


def test_opening_a_legacy_archive_refuses_with_the_fix(db_path: Path) -> None:
    # applying the schema would part-succeed and then die on an index over a column that
    # isn't there, so every command would report raw SQL instead of what to do
    legacy_archive(db_path)
    with ShouldRaise(
        UserError(
            f'{db_path} predates the current session schema — run `ch doctor --fix -c archive-schema`'
        )
    ):
        Archive.open(db_path)
