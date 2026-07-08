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

ROLE_ENV_VAR = 'CHIMERA_ROLE'
ROLE_SCOPE_ENV_VAR = 'CHIMERA_ROLE_SCOPE'  # '<project>' or '<project>@<goal>'

# Per-role command allowlists (canonical leaf paths). A role's session sees only these —
# the rest of the tree is stripped (see __main__._strip_to_role), never admonished about.
ROLE_COMMANDS: dict[str, frozenset[str]] = {
    # captain deliberately absent: full tree (minus AI-restricted options)
    ROLE_MANAGER: frozenset(
        {
            'help',
            'ls',
            'goal ls',
            'agent ls',
            'goal start',
            'goal adopt',
            'goal sync',
            'goal finish',
            'goal rename',
            'agent start',
            'agent resume',
            'review',
        }
    ),
    ROLE_AGENT: frozenset({'help'}),  # 'prime' joins later in the arc
}


def running_under_ai_agent() -> bool:
    """True when the current process was launched by (or under) an AI coding agent."""
    return any(var in os.environ for var in _AGENT_ENV_VARS)


def session_role() -> str | None:
    """The role stamped into this session's environment, or None when unset/empty."""
    return os.environ.get(ROLE_ENV_VAR) or None


def role_scope() -> str | None:
    """The scope the session's role is fenced to, or None when unset/empty."""
    return os.environ.get(ROLE_SCOPE_ENV_VAR) or None
