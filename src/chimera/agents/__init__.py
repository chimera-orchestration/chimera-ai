"""The harnesses Chimera launches agent sessions in.

Each harness subclasses :class:`Agent` and registers in ``chimera.agents.registry``
(claude today; codex and friends to come). The split is facts vs policy: an adapter
*declares* facts — its platform name, its restricted flag spellings, what its own
registry claims is live — while the shared layer applies policy: restriction when an
AI agent is driving (``chimera.commands.agent.refuse_restricted``) and distrust of
registry claims (:meth:`Agent.live`).
"""

import os
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from subprocess import CompletedProcess
from typing import ClassVar, Protocol

from loguru import logger


@dataclass(frozen=True)
class Session:
    """A live agent session: its native id, name, status, working directory and summary.

    ``id`` is the fullest form the harness reports (claude's full session UUID when
    present) — session identity must stay ``(platform, native_id)``-shaped for the
    archive, and a short handle isn't safe to recover from. Display uses :attr:`short`.
    ``pid``/``kind``/``started`` ride along when the harness reports them (a
    server-backed harness may have no pid to claim); ``summary`` is listing enrichment.
    ``stale`` is ``None`` while there is no reason to doubt the session is live;
    otherwise it names why the entry is a corpse (a registry remnant, a dead pid) —
    marked rather than hidden, so staleness can be surfaced, not just logged.
    """

    id: str
    name: str
    status: str
    cwd: Path
    summary: str | None
    pid: int | None = None
    kind: str | None = None
    started: datetime | None = None
    stale: str | None = None

    @property
    def short(self) -> str:
        """The display form of the id: its leading 8-char block."""
        return self.id[:8]

    @property
    def detail(self) -> str:
        """One-line description: the session title or last prompt, else the cwd."""
        if self.summary:
            return self.summary
        home = str(Path.home())
        cwd = str(self.cwd)
        return '~' + cwd[len(home) :] if cwd.startswith(home) else cwd


class Launch(Protocol):
    """The call shape :meth:`Agent.start` and :meth:`Agent.resume` share.

    For code holding either as a value (chat's launch-or-revive dispatch): ``resume``'s
    extra keyword-only ``id`` has a default, so both methods satisfy this — a caller
    that resolved an id calls ``resume`` directly instead.
    """

    def __call__(
        self,
        cwd: Path,
        name: str,
        prompt: str | None = None,
        extra: Sequence[str] = (),
        dangerous: bool = False,
        *,
        model: str | None = None,
        context: Path | None = None,
        env: Mapping[str, str] = {},
        exclusive: bool = True,
    ) -> CompletedProcess[bytes]: ...


class Agent(ABC):
    """A harness that runs agent sessions: start or resume one, and list what's live.

    ``platform`` names the harness in config, flags and session records — session
    identity is ``(platform, native id)``. ``model`` picks the model for the session
    (the harness's own default when ``None``). ``context`` is a rendered launch-context
    file (see ``chimera.agents.context``) the harness injects by whatever channel it
    has — never by writing into the repo. ``dangerous`` asks the harness to make its
    permissions-bypass mode *reachable* (never active); a harness without such a mode
    ignores it. ``extra`` is forwarded to the harness binary verbatim. ``env`` is extra
    variables overlaid on the parent environment — how a launcher stamps role identity
    (``CHIMERA_ROLE``/``CHIMERA_ROLE_SCOPE``) into the session; the overlay wins over
    the parent's own values, so a captain session launching ``ch goal start`` hands the
    child ``agent``, never its own ``captain``. ``exclusive`` (the default) refuses to
    launch while any session is live in ``cwd`` — a chat deliberately sits alongside a
    working agent, so it opts out.
    """

    platform: ClassVar[str]

    restricted: ClassVar[frozenset[str]] = frozenset()
    """The harness's own permission-bypass spellings, refused in the ``--`` passthrough
    when chimera itself is driven by an AI agent (see ``commands.agent.refuse_restricted``)."""

    @abstractmethod
    def start(
        self,
        cwd: Path,
        name: str,
        prompt: str | None = None,
        extra: Sequence[str] = (),
        dangerous: bool = False,
        *,
        model: str | None = None,
        context: Path | None = None,
        env: Mapping[str, str] = {},
        exclusive: bool = True,
    ) -> CompletedProcess[bytes]:
        """Launch a new session named ``name`` in ``cwd``; background when ``prompt`` is given."""
        ...

    @abstractmethod
    def resume(
        self,
        cwd: Path,
        name: str,
        prompt: str | None = None,
        extra: Sequence[str] = (),
        dangerous: bool = False,
        *,
        id: str | None = None,
        model: str | None = None,
        context: Path | None = None,
        env: Mapping[str, str] = {},
        exclusive: bool = True,
    ) -> CompletedProcess[bytes]:
        """Reattach to a session, reviving it in ``cwd`` if dead.

        ``id`` is the harness-native session id to reattach by, re-asserting ``name``
        as the display label — names are mutable (a rename in the harness's own UI
        orphans them), so a caller that knows the id must pass it. Without one the
        name is the only handle left (the pre-archive behaviour).
        """
        ...

    @abstractmethod
    def run(
        self,
        cwd: Path,
        name: str,
        prompt: str,
        extra: Sequence[str] = (),
        *,
        model: str | None = None,
        context: Path | None = None,
        env: Mapping[str, str] = {},
        readonly: bool = True,
        timeout: float | None = None,
    ) -> str:
        """Run a one-shot headless session in ``cwd``; block and return its result text.

        The synchronous sibling of :meth:`start`: nothing to attach to and nothing left
        behind — the call returns when the run does. ``readonly`` (the default) is a
        harness-agnostic capability hint — read-only research tools, including VCS
        archaeology — that each adapter maps to its own native permission wall; no
        harness flag spellings appear at this level. ``name`` labels the run in logs (a
        headless run may have no session object to carry it). ``timeout`` is in seconds;
        ``None`` blocks until the run finishes.
        """
        ...

    @abstractmethod
    def sessions(self) -> list[Session]:
        """Every :meth:`checked` session this harness reports, enriched for listing.

        Checked, not merely live: stale entries ride along marked (``Session.stale``),
        never dropped, so the listing surface decides whether to show them.
        """
        ...

    @abstractmethod
    def reported(self, cwd: Path | None = None) -> list[Session]:
        """Every session the harness's *registry* claims is live in ``cwd`` (``None`` = anywhere).

        Claims, not facts — registries lie (see :meth:`live`). An adapter parses its
        native records into :class:`Session`, marking (never dropping) the corpses only
        it can recognise (e.g. claude's degraded pid-less remnants — see
        ``Claude.reported``) with a ``stale`` reason.
        """
        ...

    def checked(self, cwd: Path | None = None) -> list[Session]:
        """:meth:`reported` with the generic distrust applied: every claim, corpses marked.

        The verification is generic — a *claimed* pid must name a running process —
        so no adapter can forget it. An entry claiming no pid at all stands on the
        adapter's word: a server-backed harness may legitimately have none to claim.
        Stale entries stay in the list, ``stale`` naming why, so they can be surfaced;
        :meth:`live` is this minus the marked.
        """
        return [_distrusted(session) for session in self.reported(cwd)]

    def live(self, cwd: Path | None = None) -> list[Session]:
        """The sessions actually live in ``cwd``: :meth:`checked`, minus the stale."""
        return [session for session in self.checked(cwd) if session.stale is None]


def _distrusted(session: Session) -> Session:
    """The session, marked stale if its claimed pid names no running process.

    ``os.kill(pid, 0)`` sends no signal, only probes. A dead pid marks the entry stale
    (logged, with the session, for anyone debugging a liveness check that looked
    wrong); a pid owned by another user still proves the process exists. An entry the
    adapter already marked stale is passed through unprobed.
    """
    if session.stale is not None or session.pid is None:
        return session
    try:
        os.kill(session.pid, 0)
    except ProcessLookupError:
        logger.bind(session=str(session)).warning('agent: session pid is dead, treating as stale')
        return replace(session, stale=f'claimed pid {session.pid} is not running')
    except PermissionError:
        logger.bind(session=str(session)).info('agent: session pid owned by another user')
    return session
