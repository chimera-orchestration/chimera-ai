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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from subprocess import CompletedProcess
from typing import ClassVar

from loguru import logger


@dataclass(frozen=True)
class Session:
    """A live agent session: its native id, name, status, working directory and summary.

    ``id`` is the fullest form the harness reports (claude's full session UUID when
    present) — session identity must stay ``(platform, native_id)``-shaped for the
    archive, and a short handle isn't safe to recover from. Display uses :attr:`short`.
    ``pid``/``kind``/``started`` ride along when the harness reports them (a
    server-backed harness may have no pid to claim); ``summary`` is listing enrichment.
    """

    id: str
    name: str
    status: str
    cwd: Path
    summary: str | None
    pid: int | None = None
    kind: str | None = None
    started: datetime | None = None

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


class Agent(ABC):
    """A harness that runs agent sessions: start or resume one, and list what's live.

    ``platform`` names the harness in config, flags and session records — session
    identity is ``(platform, native id)``. ``model`` picks the model for the session
    (the harness's own default when ``None``). ``context`` is a rendered launch-context
    file (see ``chimera.agents.context``) the harness injects by whatever channel it
    has — never by writing into the repo. ``dangerous`` asks the harness to make its
    permissions-bypass mode *reachable* (never active); a harness without such a mode
    ignores it. ``extra`` is forwarded to the harness binary verbatim. ``exclusive``
    (the default) refuses to launch while any session is live in ``cwd`` — a chat
    deliberately sits alongside a working agent, so it opts out.
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
        model: str | None = None,
        context: Path | None = None,
        exclusive: bool = True,
    ) -> CompletedProcess[bytes]:
        """Reattach to the session named ``name``, reviving it in ``cwd`` if dead."""
        ...

    @abstractmethod
    def sessions(self) -> list[Session]:
        """Every verified-live session this harness is running, enriched for listing."""
        ...

    @abstractmethod
    def reported(self, cwd: Path | None = None) -> list[Session]:
        """Every session the harness's *registry* claims is live in ``cwd`` (``None`` = anywhere).

        Claims, not facts — registries lie (see :meth:`live`). An adapter parses its
        native records into :class:`Session`, applying only knowledge specific to its
        own registry (e.g. claude drops the degraded pid-less remnants its registry
        keeps briefly after a kill).
        """
        ...

    def live(self, cwd: Path | None = None) -> list[Session]:
        """:meth:`reported`, distrusted: an entry claiming a dead pid is dropped.

        The verification is generic — a *claimed* pid must name a running process —
        so no adapter can forget it. An entry claiming no pid at all stands on the
        adapter's word: a server-backed harness may legitimately have none to claim.
        """
        return [session for session in self.reported(cwd) if _claimed_pid_alive(session)]


def _claimed_pid_alive(session: Session) -> bool:
    """Whether the session's claimed pid names a process that's actually running.

    ``os.kill(pid, 0)`` sends no signal, only probes. A dead pid means the registry
    entry is stale (logged, with the session, for anyone debugging a liveness check
    that looked wrong); a pid owned by another user still proves the process exists.
    """
    if session.pid is None:
        return True  # nothing claimed, nothing to disprove
    try:
        os.kill(session.pid, 0)
    except ProcessLookupError:
        logger.bind(session=str(session)).warning('agent: session pid is dead, treating as stale')
        return False
    except PermissionError:
        logger.bind(session=str(session)).info('agent: session pid owned by another user')
    return True
