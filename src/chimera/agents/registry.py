"""The launchable harnesses, keyed by platform name.

Its own module (not ``__init__``) so each harness can import the :class:`Agent`
protocol from the package without a cycle. Adding a harness is one import and one
entry here.
"""

from chimera.agents import Agent
from chimera.agents.claude import Claude

AGENTS: dict[str, Agent] = {Claude.platform: Claude()}

DEFAULT = Claude.platform
"""The harness used when neither flag nor config names one."""
