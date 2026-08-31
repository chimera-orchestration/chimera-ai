"""The archive: the central, cross-referencing store of LLM sessions and what they did.

Logging (loguru JSONL) records *what happened*, line by line. The archive is the
index *over* those happenings — the one place that ties them together: which chat
ran where, on which harness, for which goal, which actors worked it, and when. It is
a single SQLite database in WAL mode, so any number of ``ch`` processes (agents and
humans alike) can read and write it at once: many concurrent readers, writers
serialised for milliseconds by SQLite itself. The session hooks (``ch hook``) feed
it; it is the component other commands and hooks call.

It archives *every* LLM session on the machine, not just Chimera's, keyed by
**``platform``** — the harness that ran the model and owns the native session id
(``claude``/``codex``/``aider``), so session identity is ``(platform, native_id)``,
matching :class:`chimera.agents.AgentSession`.

What it holds is deliberately narrow: **identity, location, address and lifecycle**.
Searchable history, cost and summaries are not its job — agentsview does those better,
and conflating them is what let this store's answers to "who is this session, is it
really live, how do I resume it" go quietly wrong. Chimera's own axes
(``workspace``/``project``/``goal``/``actor``) are null for a session that ran outside a
managed worktree. Two record types:

- :class:`ArchiveSession` — one run, denormalised with the axes above so a cross-reference
  is a ``WHERE``, not a join.
- :class:`Event` — one timestamped thing that happened, optionally tied to a session;
  the append-only timeline that stitches the logs to the sessions that produced them.
  Sessions are upserted to current state; events accumulate forever.

Timestamps must be timezone-aware; they are stored as ISO 8601 text and ordered
lexicographically, so keep them in a single zone (UTC) for a correct timeline.
"""

import sqlite3
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict

from loguru import logger

from chimera.config import UserError
from chimera.sqlite import Database


class ArchiveSession(BaseModel):
    """One LLM run and the axes it belongs to. Identity is ``(platform, native_id)``.

    The durable record of a session, as opposed to
    :class:`chimera.agents.AgentSession`, the live registry's view of one. This
    survives the session; that is rebuilt on every liveness check.

    ``native_id`` is the harness's own session id — the *full* form (claude's full
    UUID), never the short display handle, since it's the resume target.

    Two kinds of column, and the distinction is the point (see ``agent-docs/sessions.md``,
    *A session is reached by its address, never by its location*):

    - ``workspace``/``project``/``goal``/``actor`` are **geography**, resolved from cwd.
      They say where a session sat, never who it is, and are null outside a managed
      worktree.
    - ``address`` is the **claim**: what routes mail, fills a board slot and answers a
      resume. Only evidence writes it — a chimera launch, or inheritance across a bridge.

    ``addressable`` is the harness's verdict on whether this is a conversation at all: a
    ``claude agents`` browser draft and a one-shot ``claude -p`` both fire real session
    hooks but must never hold an address. Distinct from *having* one — a hand-launched
    session is addressable yet unaddressed, and still occupies its worktree.
    ``harness_version`` records which build produced the row, so a session recorded under
    a version chimera has never validated is itself the alarm.
    """

    model_config = ConfigDict(frozen=True)

    platform: str
    native_id: str
    status: str
    started_at: datetime
    model: str | None = None
    address: str | None = None
    addressable: bool = True
    harness_version: str | None = None
    ended_at: datetime | None = None
    cwd: Path | None = None
    transcript: Path | None = None
    workspace: str | None = None
    project: str | None = None
    goal: str | None = None
    actor: str | None = None

    @property
    def transcript_missing(self) -> bool:
        """Whether the transcript this session would resume from is gone.

        A pruned transcript is what turns ``ch agent resume`` into claude's raw "No
        conversation found" traceback, so resume targeting skips such a row. Computed,
        never stored: the path stays on the row either way, because knowing *which*
        transcript vanished is the whole value of the record afterwards.
        """
        return self.transcript is not None and not self.transcript.exists()


RECONCILED = 'reconciled'
"""The closing status of a session no harness still reports: it ended without its end
hook firing (killed, crashed, the machine rebooted), and the registry's silence is what
says so. Distinct from a clean exit's own reason, so the two are never confused."""

ACTIVE = 'active'
"""The event kind a working session lands each turn (:meth:`Archive.touch`) — the
heartbeat that keeps activity ordering honest between a start and an end."""

LAUNCH_WINDOW = timedelta(minutes=2)
"""How long a :class:`PendingLaunch` stays claimable. A session's start hook fires within
seconds of the spawn, so this is generous; the bound exists because an *unclaimed* record
is an address waiting to be handed to whatever starts in that directory next."""


class PendingLaunch(BaseModel):
    """A launch chimera is about to make: everything about the session except its id.

    The seam between "chimera decided to start a session" and "a session started". A
    launcher writes one of these *before* spawning, because it cannot write a complete
    row: a foreground launch blocks until the session exits, and a background one is
    refused the chance to choose an id at all. The session's own start hook then claims
    it and binds the identity — so the address is on record before the session's first
    turn by construction, not by winning a race.
    """

    model_config = ConfigDict(frozen=True)

    at: datetime
    platform: str
    cwd: Path
    address: str
    model: str | None = None


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
    platform        TEXT NOT NULL,
    native_id       TEXT NOT NULL,
    status          TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    model           TEXT,
    address         TEXT,
    addressable     INTEGER NOT NULL DEFAULT 1,
    harness_version TEXT,
    ended_at        TEXT,
    cwd             TEXT,
    transcript      TEXT,
    workspace       TEXT,
    project         TEXT,
    goal            TEXT,
    actor           TEXT,
    PRIMARY KEY (platform, native_id)
);
CREATE INDEX IF NOT EXISTS sessions_by_goal      ON sessions(project, goal);
CREATE INDEX IF NOT EXISTS sessions_by_workspace ON sessions(workspace);
CREATE INDEX IF NOT EXISTS sessions_by_started   ON sessions(started_at);
CREATE INDEX IF NOT EXISTS sessions_by_address   ON sessions(address);

CREATE TABLE IF NOT EXISTS events (
    at          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    detail      TEXT,
    platform    TEXT,
    native_id   TEXT,
    FOREIGN KEY (platform, native_id) REFERENCES sessions(platform, native_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS events_by_session ON events(platform, native_id, at);

CREATE TABLE IF NOT EXISTS pending_launches (
    at        TEXT NOT NULL,
    platform  TEXT NOT NULL,
    cwd       TEXT NOT NULL,
    address   TEXT NOT NULL,
    model     TEXT
);
CREATE INDEX IF NOT EXISTS pending_by_cwd ON pending_launches(platform, cwd, at);
"""


_INSERT_SESSION = """
INSERT INTO sessions
    (platform, native_id, status, started_at, model, address, addressable,
     harness_version, ended_at, cwd, transcript, workspace, project, goal, actor)
VALUES
    (:platform, :native_id, :status, :started_at, :model, :address, :addressable,
     :harness_version, :ended_at, :cwd, :transcript, :workspace, :project, :goal, :actor)
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
        """Open (creating if absent) the archive at ``path``, tuned for concurrent access.

        Refuses a database still on the pre-trim schema, naming the fix. Applying the
        schema to one would part-succeed — the ``CREATE TABLE`` is a no-op against the
        old table, then an index over a column that isn't there fails — so every command
        touching the archive would die on a raw SQL error instead of saying what to do.
        Migrating here instead was tempting and wrong: a schema rewrite is doctor's, done
        once and deliberately, not something a listing does to you by surprise.
        """
        if needs_migration(path):
            raise UserError(
                f'{path} predates the current session schema — '
                'run `ch doctor --fix -c archive-schema`'
            )
        return cls(Database.open(path, SCHEMA))

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def record_session(self, session: ArchiveSession) -> None:
        """Insert ``session``, or update it in place if ``(platform, native_id)`` is known.

        Most columns take the new value: a re-record of a known identity is a *resume*
        (or a late detail pass), never a new run. The exceptions are the facts a later
        firing knows *less* about than the first did:

        - ``started_at`` is first-write-wins — the original start time is the one fact
          only the first firing knew, and it must survive. (``ended_at`` does follow the
          new value, so a resume, recording with ``ended_at=None``, reopens a session a
          SessionEnd had closed.)
        - ``model`` keeps its last known value rather than being blanked by a firing whose
          payload omitted it (the field is optional — absent after ``/clear``, and on a
          fork).
        - ``address`` is sticky: a resume or a late detail pass carries no fresh evidence
          of who the session is, so it must never *erase* a claim the launcher established.
          Clearing one is deliberate work, not a side effect of re-recording.
        """
        self._db.execute(
            'record_session',
            f"""
            {_INSERT_SESSION}
            ON CONFLICT(platform, native_id) DO UPDATE SET
                status=excluded.status,
                model=COALESCE(excluded.model, sessions.model),
                address=COALESCE(excluded.address, sessions.address),
                addressable=excluded.addressable,
                harness_version=COALESCE(excluded.harness_version, sessions.harness_version),
                ended_at=excluded.ended_at,
                cwd=excluded.cwd, transcript=excluded.transcript,
                workspace=excluded.workspace,
                project=excluded.project, goal=excluded.goal, actor=excluded.actor
            """,
            _session_params(session),
        )

    def record_session_if_absent(self, session: ArchiveSession) -> bool:
        """Insert ``session`` unless ``(platform, native_id)`` is already known; report which.

        The atomic single-statement variant of check-then-:meth:`record_session`, for a
        writer that must never clobber an existing row (``ch archive backfill``) even
        against a hook recording the same session concurrently.
        """
        cursor = self._db.execute(
            'record_session_if_absent',
            f'{_INSERT_SESSION} ON CONFLICT(platform, native_id) DO NOTHING',
            _session_params(session),
        )
        return cursor.rowcount == 1

    def end_session(
        self, platform: str, native_id: str, at: datetime, status: str = 'ended'
    ) -> None:
        """Mark a session finished: stamp ``ended_at`` and set its closing ``status``."""
        self._db.execute(
            'end_session',
            'UPDATE sessions SET ended_at=?, status=? WHERE platform=? AND native_id=?',
            (at.isoformat(), status, platform, native_id),
        )

    def session(self, platform: str, native_id: str) -> ArchiveSession | None:
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
        workspace: str | None = None,
        project: str | None = None,
        goal: str | None = None,
        actor: str | None = None,
        active: bool | None = None,
    ) -> list[ArchiveSession]:
        """Sessions matching every axis given, oldest first.

        Each keyword narrows the result; omit one to leave that axis unconstrained.
        ``active=True`` keeps only sessions not yet ended, ``active=False`` only ended ones.
        """
        where, params = _filters(
            platform=platform,
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

    def live_session_for(self, address: str) -> ArchiveSession | None:
        """The newest still-live session holding ``address``, else ``None``.

        The address→session resolver comms routes on. Keyed on the address itself, not
        on the axes it was derived from: the axes say where a session sat, and several
        sessions can sit in one worktree — a one-shot print run, a browser draft, a
        hand-launched claude — while only one of them was ever *given* the address. Most
        recently started wins a rare tie.
        """
        row = self._db.execute(
            'live_session_for',
            'SELECT * FROM sessions WHERE address=? AND ended_at IS NULL '
            'ORDER BY started_at DESC, platform, native_id LIMIT 1',
            (address,),
        ).fetchone()
        return _row_to_session(row) if row is not None else None

    def latest_session_for(
        self,
        project: str | None,
        goal: str | None = None,
        actor: str | None = None,
        *,
        platform: str | None = None,
        address: str | None = None,
        resumable: bool = False,
    ) -> ArchiveSession | None:
        """The most recently *active* session at an address, else ``None``.

        The resume resolver: registry names are mutable (a UI rename orphans the label),
        so ``agent resume`` looks the address up here and resumes by the immutable
        ``native_id`` — dead sessions included, since resuming is how a dead session is
        revived. Activity is the session's last lifecycle event (``started_at`` for a row
        with none, e.g. a backfilled one) — never creation time, which is first-write-wins:
        an old thread the user resumed yesterday must beat a fresher-created one abandoned
        after a ``/clear``. ``platform`` narrows to the harness doing the resuming.

        A goal actor address gives all three axes, which already pin it uniquely. A
        manager address gives ``project`` only, and the captain address gives none of
        them — both leave ``goal``/``actor`` (or every axis) null, which alone can't tell
        "the manager's own session" from any other axis-less row, so ``address`` narrows
        to the exact one in that case. Unlike :meth:`sessions`, ``None`` here means "must
        be unset" (``IS NULL``), not "unconstrained", for ``project``/``goal``/``actor`` —
        a board slot needs the exact address, not a widened match.

        ``resumable`` skips sessions whose transcript has been pruned, returning the
        newest one that can actually be revived. Without it, ``ch agent resume`` hands
        claude an id it no longer knows and the user sees a raw "No conversation found"
        traceback — the failure that started all of this. Off by default: a *listing*
        wants the truth about what ran most recently, pruned or not.
        """
        clauses = 'sessions.project IS ? AND sessions.goal IS ? AND sessions.actor IS ?'
        params: list[str | None] = [project, goal, actor]
        if platform is not None:
            clauses += ' AND sessions.platform=?'
            params.append(platform)
        if address is not None:
            clauses += ' AND sessions.address=?'
            params.append(address)
        rows = self._db.execute(
            'latest_session_for',
            f"""
            SELECT sessions.*, COALESCE(MAX(events.at), sessions.started_at) AS last_active
            FROM sessions LEFT JOIN events
                ON events.platform = sessions.platform AND events.native_id = sessions.native_id
            WHERE {clauses}
            GROUP BY sessions.platform, sessions.native_id
            ORDER BY last_active DESC, sessions.platform, sessions.native_id
            """,
            params,
        ).fetchall()
        sessions = [_row_to_session(row) for row in rows]
        if not resumable:
            return sessions[0] if sessions else None
        for session in sessions:
            if not session.transcript_missing:
                return session
            logger.bind(native_id=session.native_id, transcript=str(session.transcript)).info(
                'archive: skipping a session whose transcript is gone'
            )
        return None

    def latest_open_session(
        self, platform: str, cwd: Path, *, excluding: str
    ) -> ArchiveSession | None:
        """The newest session still open in ``cwd``, ignoring ``excluding``, else ``None``.

        Answers "what was running here a moment ago" — used to presume the parent a
        branched session split off from, since the harness doesn't name it. Ordered by
        start time, so the most recent claimant wins; ``excluding`` keeps the asking
        session from finding itself, its own row having just been written.
        """
        row = self._db.execute(
            'latest_open_session',
            'SELECT * FROM sessions '
            'WHERE platform=? AND cwd=? AND native_id != ? AND ended_at IS NULL '
            'ORDER BY started_at DESC, native_id LIMIT 1',
            (platform, str(cwd), excluding),
        ).fetchone()
        return _row_to_session(row) if row is not None else None

    def recent_sessions(
        self, workspace: str, *, exclude: Sequence[str] = (), limit: int
    ) -> list[ArchiveSession]:
        """The most recently active sessions in ``workspace``, newest first, excluding every
        session at an address in ``exclude`` — the residue not claimed by any board slot
        (:func:`chimera.commands.ls.board`'s ``history`` catchall). Excluding by *address*,
        not one specific ``(platform, native_id)``, drops *every* archived
        incarnation of a claimed slot (old resumes included), not just whichever one the
        slot happened to pick as its current occupant. Same "most recently active" ordering
        as :meth:`latest_session_for`. A ``NOT IN`` alone would silently drop every
        unaddressed row too (SQL's three-valued logic: ``NULL NOT IN (...)`` is ``NULL``,
        not true), so an explicit ``IS NULL`` keeps them eligible.
        """
        clauses = 'sessions.workspace=?'
        params: list[str] = [workspace]
        if exclude:
            placeholders = ', '.join('?' for _ in exclude)
            clauses += (
                f' AND (sessions.address IS NULL OR sessions.address NOT IN ({placeholders}))'
            )
            params.extend(exclude)
        rows = self._db.execute(
            'recent_sessions',
            f"""
            SELECT sessions.*, COALESCE(MAX(events.at), sessions.started_at) AS last_active
            FROM sessions LEFT JOIN events
                ON events.platform = sessions.platform AND events.native_id = sessions.native_id
            WHERE {clauses}
            GROUP BY sessions.platform, sessions.native_id
            ORDER BY last_active DESC, sessions.platform, sessions.native_id LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [_row_to_session(row) for row in rows]

    def touch(self, platform: str, native_id: str, at: datetime) -> None:
        """Record that a session is still working, as of ``at``.

        Activity ordering — which session a resume should revive, which rows are the
        recent ones — comes from ``MAX(events.at)``, and lifecycle events only fire when
        a session starts or ends. A session running for thirty-five hours therefore looks
        frozen at its start, so a corpse started an hour ago outranks it. This is the
        heartbeat that stops that: one row per turn, appended by the hook that already
        runs per turn to deliver mail. No new hook, no polling, no model turn.
        """
        self.record_event(Event(at=at, kind=ACTIVE, platform=platform, native_id=native_id))

    def record_launch(self, launch: PendingLaunch) -> None:
        """Put a launch on record before it is made (see :class:`PendingLaunch`)."""
        self._db.execute(
            'record_launch',
            'INSERT INTO pending_launches (at, platform, cwd, address, model) VALUES (?, ?, ?, ?, ?)',
            (
                launch.at.isoformat(),
                launch.platform,
                str(launch.cwd),
                launch.address,
                launch.model,
            ),
        )

    def claim_launch(self, platform: str, cwd: Path, *, now: datetime) -> PendingLaunch | None:
        """Take the newest fresh launch pending in ``cwd``, removing it; ``None`` if none.

        Claiming *consumes* the record, so two sessions starting in one directory can't
        both take the same address — the second finds nothing and stays unaddressed,
        which is the safe way to be wrong.

        Only launches within :data:`LAUNCH_WINDOW` of ``now`` count. A launch that never
        produced a session (the harness binary was missing, the user hit Ctrl-C) would
        otherwise sit there indefinitely, waiting to hand its address to whatever started
        in that directory next — granting a claim on no evidence, which is the one thing
        this design exists to prevent. Stale records are swept on the way past.
        """
        self._db.execute(
            'sweep_launches',
            'DELETE FROM pending_launches WHERE platform=? AND cwd=? AND at < ?',
            (platform, str(cwd), (now - LAUNCH_WINDOW).isoformat()),
        )
        row = self._db.execute(
            'claim_launch',
            'SELECT rowid, * FROM pending_launches WHERE platform=? AND cwd=? '
            'ORDER BY at DESC, rowid DESC LIMIT 1',
            (platform, str(cwd)),
        ).fetchone()
        if row is None:
            return None
        self._db.execute(
            'claim_launch: consume', 'DELETE FROM pending_launches WHERE rowid=?', (row['rowid'],)
        )
        return PendingLaunch(
            at=datetime.fromisoformat(row['at']),
            platform=row['platform'],
            cwd=Path(row['cwd']),
            address=row['address'],
            model=row['model'],
        )

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


def archive(workspace: Path) -> Archive:
    """The workspace's session archive, at ``state/archive.db``."""
    return Archive.open(workspace / 'state' / 'archive.db')


LEGACY_COLUMNS: frozenset[str] = frozenset(
    {'manager', 'name', 'summary', 'input_tokens', 'output_tokens', 'cost_usd'}
)
"""Columns of the pre-trim schema. The archive kept searchable history, cost and
summaries beside identity; agentsview does those better, and the conflation is what let
identity go quietly wrong. Presence of any one of these marks a database as pre-trim."""


def needs_migration(path: Path) -> bool:
    """Whether the archive at ``path`` still carries the pre-trim schema.

    Read-only and safe on a missing file, so doctor can ask before deciding to touch
    anything. A database chimera has never created answers ``False``: there is nothing
    to migrate.
    """
    if not path.exists():
        return False
    with Database.open(path) as db:
        return bool(LEGACY_COLUMNS & _columns(db, 'sessions'))


def events_orphaned(path: Path) -> bool:
    """Whether ``events`` references a table that no longer exists.

    The wreckage a rebuild leaves when ``ALTER TABLE … RENAME`` rewrites the foreign key
    to follow the rename and the renamed table is then dropped. It is invisible to
    :func:`needs_migration` — the ``sessions`` columns look entirely current — and fatal:
    every append to the timeline fails, so hooks, heartbeats and reconciliation all stop
    while the archive reports itself healthy.

    Asked of the reference rather than of a remembered table name, so it still holds if a
    future rebuild breaks the same way under a different name.
    """
    if not path.exists():
        return False
    with Database.open(path) as db:
        tables = {
            row['name']
            for row in db.execute(
                'tables', "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        keys = db.execute('events keys', 'PRAGMA foreign_key_list(events)').fetchall()
    return any(row['table'] not in tables for row in keys)


def repair_events(path: Path) -> int:
    """Rebuild ``events`` onto a resolving foreign key; return the events carried over.

    Same shape as :func:`migrate`'s rebuild and the same two pragmas, for the same two
    reasons — the drop must not cascade, and the rename must not rewrite anything.
    """
    with Database.open(path) as db:
        if not events_orphaned(path):
            return 0
        db.executescript(
            'repair: rebuild events',
            f"""
            PRAGMA foreign_keys=OFF;
            PRAGMA legacy_alter_table=ON;
            ALTER TABLE events RENAME TO events_legacy;
            {SCHEMA}
            INSERT INTO events (at, kind, detail, platform, native_id)
            SELECT at, kind, detail, platform, native_id FROM events_legacy;
            DROP TABLE events_legacy;
            PRAGMA legacy_alter_table=OFF;
            PRAGMA foreign_keys=ON;
            """,
        )
        counted = db.execute('repair: count', 'SELECT COUNT(*) AS n FROM events')
        return int(counted.fetchone()['n'])


def migrate(path: Path) -> int:
    """Rebuild a pre-trim archive onto the current schema; return the rows carried over.

    A rebuild rather than ``ALTER TABLE … DROP COLUMN``, because the FTS triggers
    reference the very columns being dropped and SQLite refuses while they do — so the
    triggers and the ``sessions_fts`` virtual table go first, then a fresh table takes
    the surviving columns and is renamed into place.

    **Two pragmas guard the rename, and both are load-bearing.** ``ALTER TABLE … RENAME``
    rewrites referencing keys to follow the rename, so ``events`` comes to point at
    ``sessions_legacy``. That single mechanism breaks the history twice over, and each
    half needs its own pragma:

    - ``foreign_keys=OFF`` stops the ``DROP`` cascade-deleting every event. Caught against
      a real 247-session archive, which lost all 262 of its events.
    - ``legacy_alter_table=ON`` stops the rewrite happening at all, so ``events`` keeps
      pointing at ``sessions`` — the table ``SCHEMA`` recreates. Without it the rebuild
      succeeds, the events survive the drop, and the archive is left referencing a table
      that no longer exists: every later ``INSERT INTO events`` fails with ``no such
      table: main.sessions_legacy``, so hooks, heartbeats and reconciliation all die the
      moment the migration is declared a success.

    The second was missed because reasoning stopped at the cascade, and because every test
    here read after migrating and none wrote. The events are the append-only history this
    whole design exists to keep; a migration must leave them writable, not merely present.

    The dying ``manager`` column earns its keep on the way out. It recorded whether a
    *launcher* stamped the session, which is exactly the evidence the new ``address``
    demands — so a manager/captain claim survives only where ``manager='chimera'`` or the
    axes name a goal worktree (where the address follows the actor, not a guess). Every
    other historical claim was inferred from geography alone, and geography never
    entitled a session to an address: those are nulled, applying the rule retroactively
    instead of grandfathering claims the current code would refuse to make.
    """
    with Database.open(path) as db:
        if not (LEGACY_COLUMNS & _columns(db, 'sessions')):
            return 0
        db.executescript(
            'migrate: drop legacy search',
            """
            DROP TRIGGER IF EXISTS sessions_ai;
            DROP TRIGGER IF EXISTS sessions_ad;
            DROP TRIGGER IF EXISTS sessions_au;
            DROP TABLE IF EXISTS sessions_fts;
            DROP INDEX IF EXISTS sessions_by_manager;
            """,
        )
        db.executescript(
            'migrate: rebuild sessions',
            f"""
            PRAGMA foreign_keys=OFF;
            PRAGMA legacy_alter_table=ON;
            ALTER TABLE sessions RENAME TO sessions_legacy;
            {SCHEMA}
            INSERT INTO sessions
                (platform, native_id, status, started_at, model, address, addressable,
                 harness_version, ended_at, cwd, transcript, workspace, project, goal, actor)
            SELECT platform, native_id, status, started_at, model,
                   CASE WHEN manager = 'chimera' OR (goal IS NOT NULL AND actor IS NOT NULL)
                        THEN name ELSE NULL END,
                   1, NULL, ended_at, cwd, transcript, workspace, project, goal, actor
            FROM sessions_legacy;
            DROP TABLE sessions_legacy;
            PRAGMA legacy_alter_table=OFF;
            PRAGMA foreign_keys=ON;
            """,
        )
        counted = db.execute('migrate: count', 'SELECT COUNT(*) AS n FROM sessions')
        return int(counted.fetchone()['n'])


def _columns(db: Database, table: str) -> set[str]:
    return {row['name'] for row in db.execute('columns', f'PRAGMA table_info({table})').fetchall()}


def _filters(
    *,
    platform: str | None,
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


def _session_params(session: ArchiveSession) -> dict[str, object]:
    return {
        'platform': session.platform,
        'native_id': session.native_id,
        'status': session.status,
        'started_at': session.started_at.isoformat(),
        'model': session.model,
        'address': session.address,
        'addressable': session.addressable,
        'harness_version': session.harness_version,
        'ended_at': session.ended_at.isoformat() if session.ended_at is not None else None,
        'cwd': str(session.cwd) if session.cwd is not None else None,
        'transcript': str(session.transcript) if session.transcript is not None else None,
        'workspace': session.workspace,
        'project': session.project,
        'goal': session.goal,
        'actor': session.actor,
    }


def _row_to_session(row: sqlite3.Row) -> ArchiveSession:
    return ArchiveSession(
        platform=row['platform'],
        native_id=row['native_id'],
        status=row['status'],
        started_at=datetime.fromisoformat(row['started_at']),
        model=row['model'],
        address=row['address'],
        addressable=bool(row['addressable']),
        harness_version=row['harness_version'],
        ended_at=datetime.fromisoformat(row['ended_at']) if row['ended_at'] else None,
        cwd=Path(row['cwd']) if row['cwd'] else None,
        transcript=Path(row['transcript']) if row['transcript'] else None,
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
