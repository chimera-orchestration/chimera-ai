"""The harnesses Chimera launches agent sessions in.

Each harness implements the :class:`Agent` protocol and registers in
``chimera.agents.registry`` (claude today; codex and friends to come).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess
from typing import Protocol


@dataclass(frozen=True)
class Session:
    """A live agent session: its native id, name, status, working directory and summary.

    ``id`` is the fullest form the harness reports (claude's full session UUID when
    present) — session identity must stay ``(platform, native_id)``-shaped for the
    archive, and a short handle isn't safe to recover from. Display uses :attr:`short`.
    """

    id: str
    name: str
    status: str
    cwd: Path
    summary: str | None

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


class Agent(Protocol):
    """A harness that runs agent sessions: start or resume one, and list what's live.

    ``platform`` names the harness in config, flags and session records — session
    identity is ``(platform, native id)``. ``model`` picks the model for the session
    (the harness's own default when ``None``). ``context`` is a rendered launch-context
    file (see ``chimera.agents.context``) the harness injects by whatever channel it
    has — never by writing into the repo. ``dangerous`` asks the harness to make its
    permissions-bypass mode *reachable* (never active); a harness without such a mode
    ignores it. ``extra`` is forwarded to the harness binary verbatim.
    """

    platform: str

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
    ) -> CompletedProcess[bytes]:
        """Launch a new session named ``name`` in ``cwd``; background when ``prompt`` is given."""
        ...

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
    ) -> CompletedProcess[bytes]:
        """Reattach to the session named ``name``, reviving it in ``cwd`` if dead."""
        ...

    def sessions(self) -> list[Session]:
        """Every live session this harness is running, enriched for listing."""
        ...
