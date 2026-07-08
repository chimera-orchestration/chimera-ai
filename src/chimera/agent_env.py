"""Detects whether chimera is being invoked by an AI coding agent rather than a human
at an interactive terminal. Deliberately the only place that reads the process
environment for this purpose, so a future harness-agnostic detector is a one-file change.
"""

import os

_AGENT_ENV_VARS = ('CLAUDECODE',)  # Claude Code sets this for every subprocess it launches

RESTRICTED_OPTIONS = frozenset({'--force', '--dangerous'})

# The session-layer roles: the workspace captain, a project's manager (its chat), and a
# goal's agent. ROLE_-prefixed to avoid colliding with chimera.worktrees.AGENT — the same
# string on a different axis (actor naming vs session role), deliberately kept apart.
ROLE_CAPTAIN, ROLE_MANAGER, ROLE_AGENT = 'captain', 'manager', 'agent'


def running_under_ai_agent() -> bool:
    """True when the current process was launched by (or under) an AI coding agent."""
    return any(var in os.environ for var in _AGENT_ENV_VARS)
