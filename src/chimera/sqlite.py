"""A thin, traced wrapper over a SQLite connection — the seam every statement passes through.

The SQLite analogue of :class:`chimera.git.Git`: as ``Git`` traces every subprocess before it
runs, :class:`Database` traces every statement — the op and the db path, bound at DEBUG, never
the parameters or rows — so a run is reconstructable from the log without a stored row's data
ever being written to it. A SQLite-backed store (e.g. :mod:`chimera.archive`) opens one and
issues every query through :meth:`execute`; nothing touches the raw connection.
"""

import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Self

from loguru import logger

Params = Sequence[object] | Mapping[str, object]
"""Positional (tuple/list) or named (dict) statement parameters, as ``sqlite3`` accepts."""


class Database:
    """An open SQLite database, WAL-tuned for concurrent access, that traces every statement.

    Open one per process with :meth:`open` — they share the file, not the object. Use as a
    context manager, or call :meth:`close`.
    """

    def __init__(self, connection: sqlite3.Connection, path: Path) -> None:
        self._connection = connection
        self._path = path

    @classmethod
    def open(cls, path: Path, schema: str = '') -> Self:
        """Open (creating if absent) the database at ``path``, applying ``schema`` if given.

        WAL + a busy timeout so concurrent ``ch`` processes get many readers and a serialised
        writer that waits rather than failing; foreign keys on. The open itself is traced.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, isolation_level=None)  # autocommit; each write is atomic
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA busy_timeout=5000')
        connection.execute('PRAGMA foreign_keys=ON')
        if schema:
            connection.executescript(schema)
        logger.bind(db=str(path)).debug('open')
        return cls(connection, path)

    def execute(self, op: str, sql: str, params: Params = ()) -> sqlite3.Cursor:
        """Run one statement, tracing ``op`` and the db path first — never the parameters or rows.

        ``op`` is the caller's label for what the statement does (``'record_session'``); it plus
        the bound db path is the whole trace, so the log names what happened and where without
        ever capturing a stored row's data.
        """
        logger.bind(db=str(self._path)).debug(op)
        return self._connection.execute(sql, params)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
