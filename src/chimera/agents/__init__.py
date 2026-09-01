"""The harnesses Chimera launches agent sessions in.

Each harness subclasses :class:`Agent` and registers in ``chimera.agents.registry``
(claude today; codex and friends to come). The split is facts vs policy: an adapter
*declares* facts — its platform name, its restricted flag spellings, what its own
registry claims is live — while the shared layer applies policy: restriction when an
AI agent is driving (``chimera.commands.agent.refuse_restricted``) and distrust of
registry claims (:meth:`Agent.live`).
"""

import os
import signal
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Protocol

from loguru import logger

from chimera.config import UserError
from chimera.processes import process_create_time, same_process

STARTUP, RESUME, BRANCHED = 'startup', 'resume', 'branched'
"""What :meth:`Agent.lifecycle` may answer — chimera's vocabulary, not any harness's.
``BRANCHED`` is the one that carries weight: a session split off another inherits an
address it was never launched with, so it must be told apart from a cold start."""


@dataclass(frozen=True)
class AgentSession:
    """A live agent session: its native id, name, status, working directory and summary.

    The *registry* view of a session — what a harness claims is running right now —
    as opposed to :class:`chimera.archive.Session`, the durable record of one. The
    two are deliberately separate types: this one is rebuilt from scratch on every
    liveness check and holds only what a harness will vouch for.

    ``id`` is the fullest form the harness reports (claude's full session UUID when
    present) — session identity must stay ``(platform, native_id)``-shaped for the
    archive, and a short handle isn't safe to recover from. Display uses :attr:`short`.
    ``pid``/``kind``/``started`` ride along when the harness reports them (a
    server-backed harness may have no pid to claim); ``summary`` is listing enrichment.
    ``create_time`` is the pid's other half (see :func:`~chimera.processes.
    process_create_time`): filled in by :meth:`Agent.checked`, and — when a caller
    supplies one it captured earlier — the thing that catches a reused pid.
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
    create_time: float | None = None
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
    ) -> str | None: ...


class Agent(ABC):
    """A harness that runs agent sessions: start or resume one, and list what's live.

    ``platform`` names the harness in config, flags and session records — session
    identity is ``(platform, native id)``. ``model`` picks the model for the session
    (the harness's own default when ``None``). ``context`` is a rendered launch-context
    file (see ``chimera.agents.context``) the harness injects by whatever channel it
    has — never by writing into the repo. ``dangerous`` asks the harness to make its
    permissions-bypass mode *reachable* (never active); a harness without such a mode
    ignores it. ``extra`` is forwarded to the harness binary verbatim.

    Whether a launch is *allowed* — whether something else is already working in that
    worktree — is not asked here: an adapter reports what its registry claims is live
    (:meth:`live`), and which of those count as occupants is chimera's policy, applied by
    the launchers (``chimera.commands.agent.occupants``).
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
    ) -> str | None:
        """Launch a new session named ``name`` in ``cwd``; background when ``prompt`` is given.

        Returns the session's **native id** — the full, resumable form — or ``None`` when
        the harness can't be made to say. Chimera never guesses one: an id it cannot
        vouch for is worse than none, since the archive would record a session that can't
        be resumed. How the id is obtained is the harness's own business (claude mints it
        up front for a foreground launch, and can't for a background one); the caller
        only learns whether it got one.
        """
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
    ) -> str | None:
        """Revive a session in ``cwd``, continuing it from wherever it left off.

        Never attaches to a session still running — a live one is refused up front
        (see ``exclusive``) — always a resume of a dead one. ``id`` is the
        harness-native session id to resume by, re-asserting ``name`` as the display
        label — names are mutable (a rename in the harness's own UI orphans them), so
        a caller that knows the id must pass it. Without one the name is the only
        handle left (the pre-archive behaviour). Returns the revived session's native
        id, as :meth:`start` does.
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
    def session_id_from_env(self) -> str | None:
        """The id of the session this very process is running inside, if any.

        "Am I inside one of your sessions — which?", asked of the harness because only it
        knows how it marks its own children. ``None`` when this isn't one of its sessions
        (a plain shell), which is how chimera tells a human's invocation from an agent's.
        """
        ...

    @abstractmethod
    def identity(self, payload: Mapping[str, object]) -> str:
        """The native id of the session a start event names.

        The adapter reconciles whatever ids its payload and environment carry, and is
        the only place that knows which to believe when they disagree — chimera never
        sees the alternatives, only the verdict. Disagreement is logged there, loudly:
        a harness quietly changing which id is authoritative is exactly the drift that
        must not pass silently (see ``agent-docs/sessions.md``).
        """
        ...

    @abstractmethod
    def addressable(self, payload: Mapping[str, object], env: Mapping[str, str]) -> bool:
        """Whether this start event is a *conversation* — something that may hold an address.

        Harnesses fire the same session hooks for things that aren't chats (claude's
        agent browser pre-spawns a draft; a one-shot print run is a session too). Those
        must never receive mail or occupy a slot. Fails open by convention: an unknown
        shape is treated as a conversation, because a real chat losing its mail is worse
        than a draft gaining an inbox nobody writes to.
        """
        ...

    @abstractmethod
    def lifecycle(self, payload: Mapping[str, object]) -> str:
        """What this start event is: ``startup``, ``resume`` or ``branched``.

        ``branched`` covers a session split off another (claude's bridge to the
        background), which matters because such a session inherits an address it was
        never launched with. The harness's own vocabulary for this stays inside the
        adapter.
        """
        ...

    @abstractmethod
    def sessions(self) -> list[AgentSession]:
        """Every :meth:`checked` session this harness reports, enriched for listing.

        Checked, not merely live: stale entries ride along marked (``AgentSession.stale``),
        never dropped, so the listing surface decides whether to show them.
        """
        ...

    def available(self) -> bool:
        """Whether this harness can be consulted at all right now.

        Distinct from "nothing is live". A harness that cannot answer returns an empty
        listing, which is indistinguishable from an empty machine — and acting on that
        mistake is destructive: :func:`~chimera.commands.agent.reconcile` would read it
        as every session having died and close every open row. Anything that merely
        *reads* a listing may treat unavailable as empty; anything that *writes* from one
        must ask this first.
        """
        return True

    @abstractmethod
    def reported(self, cwd: Path | None = None) -> list[AgentSession]:
        """Every session the harness's *registry* claims is live in ``cwd`` (``None`` = anywhere).

        Claims, not facts — registries lie (see :meth:`live`). An adapter parses its
        native records into :class:`AgentSession`, marking (never dropping) the corpses only
        it can recognise (e.g. claude's degraded pid-less remnants — see
        ``Claude.reported``) with a ``stale`` reason.
        """
        ...

    def checked(self, cwd: Path | None = None) -> list[AgentSession]:
        """:meth:`reported` with the generic distrust applied: every claim, corpses marked.

        The verification is generic — a *claimed* pid must name a running process —
        so no adapter can forget it. An entry claiming no pid at all stands on the
        adapter's word: a server-backed harness may legitimately have none to claim.
        Stale entries stay in the list, ``stale`` naming why, so they can be surfaced;
        :meth:`live` is this minus the marked.
        """
        return [_distrusted(session) for session in self.reported(cwd)]

    def live(self, cwd: Path | None = None) -> list[AgentSession]:
        """The sessions actually live in ``cwd``: :meth:`checked`, minus the stale."""
        return [session for session in self.checked(cwd) if session.stale is None]

    def stop(self, session: AgentSession, timeout: float = 10.0) -> None:
        """Stop ``session`` for good: SIGTERM its pid, then wait for it to exit.

        The harness-agnostic default — a plain process dies cleanly on SIGTERM, with
        nothing further to reconcile. A harness whose sessions need their own graceful
        shutdown call overrides this (e.g. one with a supervisor that would otherwise
        treat a bare SIGTERM as a crash and respawn the session under it). SIGKILL is
        never sent — a session that won't die is the caller's to inspect. The caller
        has already established ``session.pid`` is not ``None``.

        The pid is **re-verified against ``create_time`` immediately before the
        signal**: however fresh the caller's liveness check was, the session may have
        exited since and the kernel handed its pid to something else. Signalling is
        the one place where acting on a stale answer is unrecoverable, so a session
        whose pair no longer matches is refused rather than killed.
        """
        assert session.pid is not None
        pid = session.pid
        if not same_process(session.create_time, process_create_time(pid)):
            raise UserError(
                f'{session.name} (pid {pid}) is no longer the process it named — '
                f'the pid has been reused; re-check what is live, then re-run'
            )
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass  # died between the liveness check and the signal — already what we wanted
        except PermissionError:
            # the liveness layer deliberately counts another user's pid as live (it proves
            # the process exists — e.g. a stale registry entry whose pid was reused after a
            # reboot)
            raise UserError(
                f'{session.name} (pid {pid}) is not ours to signal — stop it by hand, then re-run'
            ) from None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, PermissionError):
                # gone — PermissionError here means the pid we could signal a moment ago was
                # freed by our SIGTERM and already reused by another user's process
                logger.bind(session=session.name, pid=pid).info('agent stop')
                return
            time.sleep(0.05)
        raise UserError(
            f'{session.name} (pid {pid}) is still running {timeout:g}s after SIGTERM — '
            f'kill it by hand, then re-run'
        )


def _distrusted(session: AgentSession) -> AgentSession:
    """The session, marked stale unless its claimed pid still names the same process.

    Two questions, in order. **Does the pid exist?** ``os.kill(pid, 0)`` sends no
    signal, only probes; a dead pid marks the entry stale (logged, with the session,
    for anyone debugging a liveness check that looked wrong), while a pid owned by
    another user still proves the process exists. **Is it the same process?** A pid is
    a slot the kernel reuses, so a session carrying a creation time captured earlier
    is only live while that pair still matches (:func:`~chimera.processes.
    same_process`); a mismatch is a reused pid wearing a live session's name, which is
    what would otherwise get an innocent process SIGTERMed.

    Sessions arriving without a creation time — every registry claim today — are
    given the one read here, so whoever captures them can pair-match later. An entry
    the adapter already marked stale is passed through unprobed.
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
    current = process_create_time(session.pid)
    if not same_process(session.create_time, current):
        logger.bind(session=str(session), create_time=current).warning(
            'agent: session pid was reused by another process, treating as stale'
        )
        return replace(session, stale=f'claimed pid {session.pid} was reused by another process')
    return replace(session, create_time=current if current is not None else session.create_time)
