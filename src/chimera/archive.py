"""The archive: the central, cross-referencing store of LLM sessions and what they did.

Logging (loguru JSONL) records *what happened*, line by line. The archive is the
index *over* those happenings — the one place that ties them together: which chat
ran where, on which harness, for which goal, which actors worked it, and when. It is
a single SQLite database in WAL mode, so any number of ``ch`` processes (agents and
humans alike) can read and write it at once: many concurrent readers, writers
serialised for milliseconds by SQLite itself. Nothing here is wired into a command
yet — it is the component other commands and hooks call.

It archives *every* LLM session on the machine, not just Chimera's, along two
orthogonal axes:

- **``platform``** — the harness that ran the model and owns the native session id
  (``claude``/``codex``/``aider``); session identity is ``(platform, native_id)``,
  matching :class:`chimera.agents.Session`.
- **``manager``** — who orchestrated it: ``chimera``, ``gastown``, … or ``none`` when
  a human launched it directly. The two value-sets are disjoint by construction —
  a harness never appears as a manager, a manager never as a platform.

Chimera's own axes (``workspace``/``project``/``goal``/``actor``) are null for a
session that ran outside a managed worktree. Two record types:

- :class:`Session` — one run, denormalised with the axes above so a cross-reference
  is a ``WHERE``, not a join.
- :class:`Event` — one timestamped thing that happened, optionally tied to a session;
  the append-only timeline that stitches the logs to the sessions that produced them.

Timestamps must be timezone-aware; they are stored as ISO 8601 text and ordered
lexicographically, so keep them in a single zone (UTC) for a correct timeline.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict

from chimera.sqlite import Database


class Session(BaseModel):
    """One LLM run and the axes it belongs to. Identity is ``(platform, native_id)``.

    ``native_id`` is the harness's own session id — the *full* form (claude's full
    UUID), never the short display handle, since it's the resume target. ``manager``
    is the orchestrator (``none`` = a human launched it). The chimera axes are null
    outside a managed worktree; the metrics are null until known.
    """

    model_config = ConfigDict(frozen=True)

    platform: str
    native_id: str
    status: str
    started_at: datetime
    manager: str = 'none'
    model: str | None = None
    name: str | None = None
    ended_at: datetime | None = None
    cwd: Path | None = None
    transcript: Path | None = None
    summary: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    workspace: str | None = None
    project: str | None = None
    goal: str | None = None
    actor: str | None = None


class Event(BaseModel):
    """One timestamped happening, optionally tied to a session by ``(platform, native_id)``."""

    model_config = ConfigDict(frozen=True)

    at: datetime
    kind: str
    detail: str | None = None
    platform: str | None = None
    native_id: str | None = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
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
CREATE INDEX IF NOT EXISTS sessions_by_goal      ON sessions(project, goal);
CREATE INDEX IF NOT EXISTS sessions_by_workspace ON sessions(workspace);
CREATE INDEX IF NOT EXISTS sessions_by_manager   ON sessions(manager);
CREATE INDEX IF NOT EXISTS sessions_by_started   ON sessions(started_at);

CREATE TABLE IF NOT EXISTS events (
    at          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    detail      TEXT,
    platform    TEXT,
    native_id   TEXT,
    FOREIGN KEY (platform, native_id) REFERENCES sessions(platform, native_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS events_by_session ON events(platform, native_id, at);

-- Full-text search over the human-meaningful columns, kept in lockstep with
-- `sessions` by triggers (the canonical FTS5 external-content idiom). The composite
-- primary key leaves the table an ordinary rowid table, so content_rowid='rowid' holds.
CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
    name, goal, project, summary,
    content='sessions', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS sessions_ai AFTER INSERT ON sessions BEGIN
    INSERT INTO sessions_fts(rowid, name, goal, project, summary)
    VALUES (new.rowid, new.name, new.goal, new.project, new.summary);
END;
CREATE TRIGGER IF NOT EXISTS sessions_ad AFTER DELETE ON sessions BEGIN
    INSERT INTO sessions_fts(sessions_fts, rowid, name, goal, project, summary)
    VALUES ('delete', old.rowid, old.name, old.goal, old.project, old.summary);
END;
CREATE TRIGGER IF NOT EXISTS sessions_au AFTER UPDATE ON sessions BEGIN
    INSERT INTO sessions_fts(sessions_fts, rowid, name, goal, project, summary)
    VALUES ('delete', old.rowid, old.name, old.goal, old.project, old.summary);
    INSERT INTO sessions_fts(rowid, name, goal, project, summary)
    VALUES (new.rowid, new.name, new.goal, new.project, new.summary);
END;
"""


class Archive:
    """A connection to the archive database. Open one per process; share the file, not this.

    Each ``ch`` invocation opens its own ``Archive`` on the same path; WAL mode lets
    them proceed concurrently. Use as a context manager, or call :meth:`close`.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    @classmethod
    def open(cls, path: Path) -> Self:
        """Open (creating if absent) the archive at ``path``, tuned for concurrent access."""
        return cls(Database.open(path, SCHEMA))

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def record_session(self, session: Session) -> None:
        """Insert ``session``, or update it in place if ``(platform, native_id)`` is known."""
        self._db.execute(
            'record_session',
            """
            INSERT INTO sessions
                (platform, native_id, status, started_at, manager, model, name, ended_at,
                 cwd, transcript, summary, input_tokens, output_tokens, cost_usd,
                 workspace, project, goal, actor)
            VALUES
                (:platform, :native_id, :status, :started_at, :manager, :model, :name, :ended_at,
                 :cwd, :transcript, :summary, :input_tokens, :output_tokens, :cost_usd,
                 :workspace, :project, :goal, :actor)
            ON CONFLICT(platform, native_id) DO UPDATE SET
                status=excluded.status, started_at=excluded.started_at, manager=excluded.manager,
                model=excluded.model, name=excluded.name, ended_at=excluded.ended_at,
                cwd=excluded.cwd, transcript=excluded.transcript, summary=excluded.summary,
                input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens,
                cost_usd=excluded.cost_usd, workspace=excluded.workspace,
                project=excluded.project, goal=excluded.goal, actor=excluded.actor
            """,
            _session_params(session),
        )

    def end_session(
        self, platform: str, native_id: str, at: datetime, status: str = 'ended'
    ) -> None:
        """Mark a session finished: stamp ``ended_at`` and set its closing ``status``."""
        self._db.execute(
            'end_session',
            'UPDATE sessions SET ended_at=?, status=? WHERE platform=? AND native_id=?',
            (at.isoformat(), status, platform, native_id),
        )

    def session(self, platform: str, native_id: str) -> Session | None:
        """The session with this identity, or ``None`` if the archive has never seen it."""
        row = self._db.execute(
            'session',
            'SELECT * FROM sessions WHERE platform=? AND native_id=?',
            (platform, native_id),
        ).fetchone()
        return _row_to_session(row) if row is not None else None

    def sessions(
        self,
        *,
        platform: str | None = None,
        manager: str | None = None,
        workspace: str | None = None,
        project: str | None = None,
        goal: str | None = None,
        actor: str | None = None,
        active: bool | None = None,
    ) -> list[Session]:
        """Sessions matching every axis given, oldest first.

        Each keyword narrows the result; omit one to leave that axis unconstrained.
        ``active=True`` keeps only sessions not yet ended, ``active=False`` only ended ones.
        """
        where, params = _filters(
            platform=platform,
            manager=manager,
            workspace=workspace,
            project=project,
            goal=goal,
            actor=actor,
            active=active,
        )
        rows = self._db.execute(
            'sessions',
            f'SELECT * FROM sessions{where} ORDER BY started_at, platform, native_id',
            params,
        ).fetchall()
        return [_row_to_session(row) for row in rows]

    def actors_for_goal(self, project: str, goal: str) -> list[str]:
        """The distinct actors who worked a goal — "which agents worked on it", answered."""
        rows = self._db.execute(
            'actors_for_goal',
            'SELECT DISTINCT actor FROM sessions '
            'WHERE project=? AND goal=? AND actor IS NOT NULL ORDER BY actor',
            (project, goal),
        ).fetchall()
        return [row['actor'] for row in rows]

    def live_session_for(self, project: str, goal: str, actor: str) -> Session | None:
        """The newest still-live session for a ``project@goal@actor`` address, else ``None``.

        The address→session resolver comms routes on: an actor address maps to whichever
        of its sessions is currently live (most recently started wins a rare tie).
        """
        row = self._db.execute(
            'live_session_for',
            'SELECT * FROM sessions '
            'WHERE project=? AND goal=? AND actor=? AND ended_at IS NULL '
            'ORDER BY started_at DESC, platform, native_id LIMIT 1',
            (project, goal, actor),
        ).fetchone()
        return _row_to_session(row) if row is not None else None

    def record_event(self, event: Event) -> None:
        """Append ``event`` to the timeline."""
        self._db.execute(
            'record_event',
            'INSERT INTO events (at, kind, detail, platform, native_id) VALUES (?, ?, ?, ?, ?)',
            (event.at.isoformat(), event.kind, event.detail, event.platform, event.native_id),
        )

    def events(self, *, platform: str | None = None, native_id: str | None = None) -> list[Event]:
        """The timeline, oldest first — every event, or only those for one session."""
        clauses: list[str] = []
        params: list[str] = []
        for column, value in (('platform', platform), ('native_id', native_id)):
            if value is not None:
                clauses.append(f'{column}=?')
                params.append(value)
        where = f' WHERE {" AND ".join(clauses)}' if clauses else ''
        rows = self._db.execute(
            'events', f'SELECT * FROM events{where} ORDER BY at, rowid', params
        ).fetchall()
        return [_row_to_event(row) for row in rows]

    def search(self, query: str) -> list[Session]:
        """Sessions whose name, goal, project or summary match the FTS5 ``query``, best first."""
        rows = self._db.execute(
            'search',
            """
            SELECT sessions.* FROM sessions
            JOIN sessions_fts ON sessions.rowid = sessions_fts.rowid
            WHERE sessions_fts MATCH ?
            ORDER BY rank
            """,
            (query,),
        ).fetchall()
        return [_row_to_session(row) for row in rows]


def _filters(
    *,
    platform: str | None,
    manager: str | None,
    workspace: str | None,
    project: str | None,
    goal: str | None,
    actor: str | None,
    active: bool | None,
) -> tuple[str, list[str]]:
    """Build the ``WHERE`` clause and params for :meth:`Archive.sessions` from set axes."""
    clauses: list[str] = []
    params: list[str] = []
    for column, value in (
        ('platform', platform),
        ('manager', manager),
        ('workspace', workspace),
        ('project', project),
        ('goal', goal),
        ('actor', actor),
    ):
        if value is not None:
            clauses.append(f'{column}=?')
            params.append(value)
    if active is not None:
        clauses.append('ended_at IS NULL' if active else 'ended_at IS NOT NULL')
    return (f' WHERE {" AND ".join(clauses)}' if clauses else '', params)


def _session_params(session: Session) -> dict[str, object]:
    return {
        'platform': session.platform,
        'native_id': session.native_id,
        'status': session.status,
        'started_at': session.started_at.isoformat(),
        'manager': session.manager,
        'model': session.model,
        'name': session.name,
        'ended_at': session.ended_at.isoformat() if session.ended_at is not None else None,
        'cwd': str(session.cwd) if session.cwd is not None else None,
        'transcript': str(session.transcript) if session.transcript is not None else None,
        'summary': session.summary,
        'input_tokens': session.input_tokens,
        'output_tokens': session.output_tokens,
        'cost_usd': session.cost_usd,
        'workspace': session.workspace,
        'project': session.project,
        'goal': session.goal,
        'actor': session.actor,
    }


def _row_to_session(row: sqlite3.Row) -> Session:
    return Session(
        platform=row['platform'],
        native_id=row['native_id'],
        status=row['status'],
        started_at=datetime.fromisoformat(row['started_at']),
        manager=row['manager'],
        model=row['model'],
        name=row['name'],
        ended_at=datetime.fromisoformat(row['ended_at']) if row['ended_at'] else None,
        cwd=Path(row['cwd']) if row['cwd'] else None,
        transcript=Path(row['transcript']) if row['transcript'] else None,
        summary=row['summary'],
        input_tokens=row['input_tokens'],
        output_tokens=row['output_tokens'],
        cost_usd=row['cost_usd'],
        workspace=row['workspace'],
        project=row['project'],
        goal=row['goal'],
        actor=row['actor'],
    )


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        at=datetime.fromisoformat(row['at']),
        kind=row['kind'],
        detail=row['detail'],
        platform=row['platform'],
        native_id=row['native_id'],
    )
