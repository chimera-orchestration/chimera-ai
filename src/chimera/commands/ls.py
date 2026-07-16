from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from chimera.agent_env import ROLE_MANAGER
from chimera.agents import Session
from chimera.archive import Archive
from chimera.archive import Session as ArchivedSession
from chimera.commands.agent import in_goal, scoped, under
from chimera.comms import Comms
from chimera.config import workspace_config
from chimera.context import Scope, iter_projects
from chimera.worktrees import AGENT, SEP, goals, session_name, worktree_actor

HISTORY_LIMIT = 10
"""Rows shown in the archive catchall before the ``-v``-style withheld hint kicks in."""


@dataclass(frozen=True)
class Mail:
    """Outstanding mail for one address, bucketed by mailbox state."""

    new: int
    cur: int
    done: int


_NO_MAIL = Mail(0, 0, 0)


@dataclass(frozen=True)
class Row:
    """One structural slot's occupant: a live session, the latest archived one, or neither.

    ``live`` (from the harness registry) wins when present; ``last`` (the archive's most
    recently active session at this address) fills in when nothing's running. Both
    ``None`` means the slot has never been occupied.
    """

    address: str
    live: Session | None
    last: ArchivedSession | None
    mail: Mail


@dataclass(frozen=True)
class GoalBoard:
    """A goal in flight and the actors working it."""

    name: str
    actors: list[Row]


@dataclass(frozen=True)
class ProjectBoard:
    """A project: its manager, its goals, and any in-scope agents not tied to a goal worktree."""

    name: str
    manager: Row
    goals: list[GoalBoard]
    loose: list[Session]


@dataclass(frozen=True)
class Board:
    """The whole picture within a scope: the captain, projects, their goals, and strays.

    ``history`` is the archive's catchall — recently active sessions not claimed by any
    slot above (captain/manager/goal actor), capped at :data:`HISTORY_LIMIT`;
    ``history_withheld`` is ``1`` when more exist beyond the cap, else ``0``.
    """

    workspace: str
    captain: Row
    projects: list[ProjectBoard]
    loose: list[Session]
    history: list[Row]
    history_withheld: int


def _mail_map(mail: Comms) -> dict[str, Mail]:
    """Every address's outstanding mail, computed in one pass over the whole mail store."""
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for state, message in mail.messages():
        counts[message.to][state] += 1
    return {address: Mail(c['new'], c['cur'], c['done']) for address, c in counts.items()}


def _named_row(
    address: str,
    universe: list[Session],
    archive: Archive,
    mail_map: dict[str, Mail],
    *,
    project: str | None,
) -> Row:
    """The captain/manager slot at ``address``: a session chimera itself named at launch
    (``--name``, matched by the registry's own ``name``), else the archive's latest session
    recorded under that same ``name`` — project/goal/actor alone can't pin the captain/manager
    address (every axis-less row would match), so identity here rides ``name`` itself (see
    :meth:`Archive.latest_session_for`).
    """
    live = next((a for a in universe if a.name == address), None)
    last = None if live is not None else archive.latest_session_for(project, name=address)
    return Row(address, live, last, mail_map.get(address, _NO_MAIL))


def _actor_row(
    project: str,
    goal: str,
    actor: str,
    live_in_goal: list[Session],
    worktrees: Path,
    archive: Archive,
    mail_map: dict[str, Mail],
) -> Row:
    """A goal actor's slot: any session physically in its ``<goal>@<actor>`` worktree fills
    it, chimera-launched or not (a worktree is a strong signal, unlike the captain/manager's
    cwd-wide inference) — else the latest archived session at the address.
    """
    address = session_name(project, goal, actor)
    live = next((a for a in live_in_goal if worktree_actor(a.cwd, worktrees) == actor), None)
    last = None if live is not None else archive.latest_session_for(project, goal, actor)
    return Row(address, live, last, mail_map.get(address, _NO_MAIL))


def board(scope: Scope, listing: list[Session], archive: Archive, mail: Comms) -> Board:
    """Partition in-scope sessions into captain → projects → manager/goals, surfacing strays
    as ``loose`` and archive-only history as ``history``.

    Every *live* session lands exactly once, same as before: under its goal actor when its
    cwd is in a goal worktree, else as project ``loose`` (e.g. a session in ``repo/``), else
    as board ``loose`` (under the workspace but no project) — a running agent is never
    dropped. New: the captain and every project's manager always get a row, live or not
    (sourced from the archive when nothing's running there), and ``history`` surfaces
    archived sessions that don't fill any of those slots.
    """
    universe = scoped(listing, scope, otherwise=scope.workspace)
    mail_map = _mail_map(mail)
    claimed: list[str] = []

    def _claim(row: Row) -> None:
        # by address, not the one (platform, native_id) a slot happened to pick as its
        # current occupant — so every archived incarnation under that name drops out of
        # history, not just whichever row filled the slot.
        if row.live is not None or row.last is not None:
            claimed.append(row.address)

    captain_address = workspace_config(scope.workspace).captain.name
    captain = _named_row(captain_address, universe, archive, mail_map, project=None)
    _claim(captain)

    projects = [scope.project] if scope.project is not None else iter_projects(scope.workspace)
    boards: list[ProjectBoard] = []
    placed: set[Session] = set()
    for p in projects:
        manager = _named_row(
            f'{p.name}{SEP}{ROLE_MANAGER}', universe, archive, mail_map, project=p.name
        )
        _claim(manager)

        names = sorted(goals(p.worktrees))
        if scope.goal is not None:
            names = [g for g in names if g == scope.goal]
        goal_boards: list[GoalBoard] = []
        in_goal_sessions: set[Session] = set()
        for g in names:
            live_in_goal = [a for a in universe if in_goal(a.cwd, p.worktrees, g)]
            in_goal_sessions.update(live_in_goal)
            actors: set[str] = {AGENT}  # the goal's existence is its <goal>@agent worktree
            actors |= {
                actor
                for a in live_in_goal
                if (actor := worktree_actor(a.cwd, p.worktrees)) is not None
            }
            actors |= set(archive.actors_for_goal(p.name, g))
            rows = [
                _actor_row(p.name, g, actor, live_in_goal, p.worktrees, archive, mail_map)
                for actor in sorted(actors)
            ]
            for row in rows:
                _claim(row)
            goal_boards.append(GoalBoard(g, rows))
        in_project = [a for a in universe if under(a.cwd, p.dir)]
        placed.update(in_project)
        boards.append(
            ProjectBoard(
                p.name, manager, goal_boards, [a for a in in_project if a not in in_goal_sessions]
            )
        )

    recent = archive.recent_sessions(scope.workspace.name, exclude=claimed, limit=HISTORY_LIMIT + 1)
    history = [
        Row(
            s.name if s.name is not None else s.native_id,
            None,
            s,
            mail_map.get(s.name, _NO_MAIL) if s.name is not None else _NO_MAIL,
        )
        for s in recent[:HISTORY_LIMIT]
    ]
    return Board(
        scope.workspace.name,
        captain,
        boards,
        [a for a in universe if a not in placed],
        history,
        1 if len(recent) > HISTORY_LIMIT else 0,
    )
