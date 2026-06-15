import sqlite3

from testfixtures import LogCapture, TempDir
from testfixtures.loguru import LoguruSource

from chimera.sqlite import Database

SCHEMA = 'CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT);'


def _trace() -> LogCapture:
    """Capture everything, DEBUG included — the statement trace is what these tests cover."""
    return LogCapture(LoguruSource(('message', 'extra')))


def test_open_creates_the_file_and_its_parents(tmpdir: TempDir) -> None:
    nested = tmpdir.path / 'a' / 'b' / 'x.db'
    with Database.open(nested):
        pass
    assert nested.exists()


def test_open_enables_wal_for_concurrent_access(tmpdir: TempDir) -> None:
    with Database.open(tmpdir.path / 'x.db'):
        pass
    fresh = sqlite3.connect(tmpdir.path / 'x.db')
    assert fresh.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    fresh.close()


def test_open_applies_the_schema_and_execute_runs_statements(tmpdir: TempDir) -> None:
    with Database.open(tmpdir.path / 'x.db', SCHEMA) as db:
        db.execute('insert', 'INSERT INTO t (v) VALUES (?)', ('hi',))
        assert db.execute('select', 'SELECT v FROM t').fetchone()['v'] == 'hi'


def test_open_is_traced_with_the_db_path(tmpdir: TempDir) -> None:
    path = tmpdir.path / 'x.db'
    with _trace() as log:
        Database.open(path).close()
    log.check(('open', {'db': str(path)}))


def test_every_statement_is_traced_with_its_op_and_db_path(tmpdir: TempDir) -> None:
    path = tmpdir.path / 'x.db'
    with Database.open(path, SCHEMA) as db:  # opened outside the capture, so 'open' isn't caught
        with _trace() as log:
            db.execute('record_thing', 'SELECT 1')
        log.check(('record_thing', {'db': str(path)}))


def test_the_trace_carries_the_op_never_the_content(tmpdir: TempDir) -> None:
    path = tmpdir.path / 'x.db'
    with Database.open(path, SCHEMA) as db:
        with _trace() as log:
            db.execute('insert', 'INSERT INTO t (v) VALUES (?)', ('TOP-SECRET',))
        # check() pins the whole captured line: op + db path only — the parameter never appears
        log.check(('insert', {'db': str(path)}))
