"""Detects whether chimera is being invoked by an AI coding agent rather than a human
at an interactive terminal. Deliberately the only place that reads the process
environment for this purpose, so a future harness-agnostic detector is a one-file change.
"""

import os

_AGENT_ENV_VARS = ('CLAUDECODE',)  # Claude Code sets this for every subprocess it launches

RESTRICTED_OPTIONS = frozenset({'--force', '--dangerous'})


def running_under_ai_agent() -> bool:
    """True when the current process was launched by (or under) an AI coding agent."""
    return any(var in os.environ for var in _AGENT_ENV_VARS)
