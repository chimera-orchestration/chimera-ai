"""The launchable harnesses, keyed by platform name, and how a launch picks one.

Its own module (not ``__init__``) so each harness can import the :class:`Agent`
protocol from the package without a cycle. Adding a harness is one import and one
entry here.
"""

from dataclasses import dataclass

from chimera.agents import Agent
from chimera.agents.claude import Claude
from chimera.config import AgentConfig, UserError

AGENTS: dict[str, Agent] = {Claude.platform: Claude()}

DEFAULT = Claude.platform
"""The harness used when neither flag nor config names one."""


class UnknownHarnessError(UserError):
    def __init__(self, name: str) -> None:
        super().__init__(f"no harness '{name}' (available: {', '.join(sorted(AGENTS))})")


@dataclass(frozen=True)
class AgentSpec:
    """Which harness runs a session, and on which model (``None`` = harness default)."""

    harness: str = DEFAULT
    model: str | None = None

    @property
    def agent(self) -> Agent:
        return AGENTS[self.harness]


def resolve_spec(harness: str | None, model: str | None, *levels: AgentConfig | None) -> AgentSpec:
    """The agent to launch: flags, then each config level, most specific first.

    Each field resolves independently — the nearest level that sets it wins — so a
    project can pin just a model while the harness comes from the workspace (mixing a
    model into a *different* harness is on whoever wrote the config; the harness will
    reject a model it doesn't know). ``levels`` are ``agent:`` blocks from config,
    most specific first; ``None`` levels (no config at that axis) are skipped.
    A resolved harness name must be registered — a typo raises, never falls through.
    """
    present = [level for level in levels if level is not None]
    harnesses = (harness, *(level.harness for level in present))
    models = (model, *(level.model for level in present))
    resolved = next((name for name in harnesses if name), DEFAULT)
    if resolved not in AGENTS:
        raise UnknownHarnessError(resolved)
    return AgentSpec(resolved, next((m for m in models if m), None))
