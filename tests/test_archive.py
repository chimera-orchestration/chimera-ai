import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Thread

import pytest
from testfixtures import TempDir

from chimera.archive import Archive, Event, Session

NOON = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def make_session(
    native_id: str,
    *,
    platform: str = 'claude',
    manager: str = 'none',
    status: str = 'running',
    started_at: datetime = NOON,
    ended_at: datetime | None = None,
    model: str | None = None,
    name: str | None = None,
    cwd: Path | None = None,
    transcript: Path | None = None,
    summary: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
    workspace: str | None = None,
    project: str | None = None,
    goal: str | None = None,
    actor: str | None = None,
) -> Session:
    return Session(
        native_id=native_id,
        platform=platform,
        manager=manager,
        status=status,
        started_at=started_at,
        ended_at=ended_at,
        model=model,
        name=name,
        cwd=cwd,
        transcript=transcript,
        summary=summary,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
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
        manager='chimera',
        model='claude-opus-4-8',
        name='chimera@logs-and-sessions@agent',
        project='chimera',
        goal='logs-and-sessions',
        actor='agent',
        cwd=Path('/work/chimera/logs-and-sessions@agent'),
        summary='building the archive',
        input_tokens=1200,
        output_tokens=340,
        cost_usd=0.051,
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
    archive.record_session(make_session('s1', status='compacting', summary='now with detail'))
    stored = archive.session('claude', 's1')
    assert stored is not None
    assert stored.status == 'compacting'
    assert stored.summary == 'now with detail'
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


def test_ending_a_session_stamps_when_and_how_it_finished(archive: Archive) -> None:
    archive.record_session(make_session('s1', status='running'))
    archive.end_session('claude', 's1', at=NOON + timedelta(hours=2), status='done')
    stored = archive.session('claude', 's1')
    assert stored is not None
    assert stored.status == 'done'
    assert stored.ended_at == NOON + timedelta(hours=2)


# --- the four questions: which chat, which goal, which agents, when ----------------


def test_sessions_for_a_goal_lists_every_chat_that_worked_it(archive: Archive) -> None:
    archive.record_session(make_session('a', project='chimera', goal='logs', actor='agent'))
    archive.record_session(make_session('b', project='chimera', goal='logs', actor='human'))
    archive.record_session(make_session('c', project='chimera', goal='docs', actor='agent'))
    on_logs = archive.sessions(project='chimera', goal='logs')
    assert [s.native_id for s in on_logs] == ['a', 'b']


def test_actors_for_a_goal_answers_which_agents_worked_on_it(archive: Archive) -> None:
    archive.record_session(make_session('a', project='chimera', goal='logs', actor='agent'))
    archive.record_session(make_session('b', project='chimera', goal='logs', actor='human'))
    archive.record_session(make_session('c', project='chimera', goal='logs', actor='agent'))
    assert archive.actors_for_goal('chimera', 'logs') == ['agent', 'human']


def test_sessions_can_be_narrowed_by_platform_and_manager(archive: Archive) -> None:
    archive.record_session(make_session('a', platform='claude', manager='chimera'))
    archive.record_session(make_session('b', platform='claude', manager='none'))
    archive.record_session(make_session('c', platform='codex', manager='chimera'))
    assert [s.native_id for s in archive.sessions(manager='chimera')] == ['a', 'c']
    assert [s.native_id for s in archive.sessions(platform='claude', manager='chimera')] == ['a']


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
    archive.record_session(make_session('now', project='chimera', goal='logs', actor='agent'))
    live = archive.live_session_for('chimera', 'logs', 'agent')
    assert live is not None
    assert live.native_id == 'now'


def test_live_session_for_prefers_the_newest_and_ignores_ended(archive: Archive) -> None:
    archive.record_session(
        make_session(
            'old',
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
            project='chimera',
            goal='logs',
            actor='agent',
            started_at=NOON + timedelta(hours=2),
        )
    )
    live = archive.live_session_for('chimera', 'logs', 'agent')
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
    assert archive.live_session_for('chimera', 'logs', 'agent') is None


def test_latest_session_for_finds_the_newest_even_when_ended(archive: Archive) -> None:
    archive.record_session(
        make_session('old', project='chimera', goal='logs', actor='agent', started_at=NOON)
    )
    archive.record_session(
        make_session(
            'new',
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
        make_session('s1', name='free-form rename', project='chimera', goal='logs', actor='agent')
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
        make_session('a', project='chimera', goal='logs', actor='agent', started_at=NOON)
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


# --- search ------------------------------------------------------------------------


def test_search_finds_sessions_by_their_summary(archive: Archive) -> None:
    archive.record_session(make_session('a', summary='wiring up the archive store'))
    archive.record_session(make_session('b', summary='fixing the doctor checks'))
    assert [s.native_id for s in archive.search('archive')] == ['a']


def test_search_matches_the_goal_name_too(archive: Archive) -> None:
    archive.record_session(make_session('a', goal='logs-and-sessions'))
    archive.record_session(make_session('b', goal='tab-completion'))
    assert [s.native_id for s in archive.search('sessions')] == ['a']


def test_search_keeps_up_when_a_session_is_updated(archive: Archive) -> None:
    archive.record_session(make_session('a', summary='draft about widgets'))
    assert archive.search('widgets')
    archive.record_session(make_session('a', summary='rewritten about gadgets'))
    assert not archive.search('widgets')
    assert [s.native_id for s in archive.search('gadgets')] == ['a']


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
