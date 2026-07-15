"""Detects whether chimera is being invoked by an AI coding agent rather than a human
at an interactive terminal. Deliberately the only place that reads the process
environment for this purpose, so a future harness-agnostic detector is a one-file change.
"""

import os

from chimera.config import UserError
from chimera.worktrees import SEP

_AGENT_ENV_VARS = ('CLAUDECODE',)  # Claude Code sets this for every subprocess it launches

RESTRICTED_OPTIONS = frozenset({'--force', '--dangerous'})

# Human-only commands (canonical leaf paths): stripped from every AI session's tree — the
# captain included, unlike the per-role allowlists — just as RESTRICTED_OPTIONS strips
# options. `logtail` is the human's live debugging surface: it blocks following the log
# until Ctrl-C, a dead end for an agent, which reads the JSONL directly instead.
RESTRICTED_COMMANDS = frozenset({'logtail'})

# The session-layer roles: the workspace captain, a project's manager (its chat), and a
# goal's agent. ROLE_-prefixed to avoid colliding with chimera.worktrees.AGENT — the same
# string on a different axis (actor naming vs session role), deliberately kept apart.
ROLE_CAPTAIN, ROLE_MANAGER, ROLE_AGENT = 'captain', 'manager', 'agent'

ROLE_ENV_VAR = 'CHIMERA_ROLE'
ROLE_SCOPE_ENV_VAR = 'CHIMERA_ROLE_SCOPE'  # '<project>' or '<project>@<goal>'

# The inter-agent mail commands — every actor (manager and agent alike) sends, reads,
# watches and retires its own mail, so both role trees carry the whole set.
_MSG_COMMANDS = frozenset(
    {
        'msg ls',
        'msg send',
        'msg inbox',
        'msg thread',
        'msg ack',
        'msg defer',
        'msg drain',
        'msg watch',
    }
)

# The harness hooks — installed user-wide, so they fire inside chimera-launched sessions too,
# where the role strip would otherwise reach them. They record to the archive and deliver
# mail; harmless as capability, and the hook process (not the agent) is what invokes them.
_HOOK_COMMANDS = frozenset({'hook session-start', 'hook session-end', 'hook deliver', 'hook stop'})

# Per-role command allowlists (canonical leaf paths). A role's session sees only these —
# the rest of the tree is stripped (see __main__._strip_to_role), never admonished about.
ROLE_COMMANDS: dict[str, frozenset[str]] = {
    # captain deliberately absent: full tree (minus AI-restricted options)
    ROLE_MANAGER: frozenset(
        {
            'help',
            'prime',
            'ls',
            'goal ls',
            'agent ls',
            'goal start',
            'goal adopt',
            'goal sync',
            'goal merge',
            'goal pr',
            'goal finish',
            'goal rename',
            'agent start',
            'agent resume',
            'agent stop',
            'review',
            'errand',
        }
    )
    | _MSG_COMMANDS
    | _HOOK_COMMANDS,
    # errand is deliberately in both trees: cross-project *reading* is knowledge, not
    # capability (same rule that leaves listers unfenced), and its target axis has its
    # own containment (see __main__._foreign)
    ROLE_AGENT: frozenset({'help', 'prime', 'errand'}) | _MSG_COMMANDS | _HOOK_COMMANDS,
}


def running_under_ai_agent() -> bool:
    """True when the current process was launched by (or under) an AI coding agent."""
    return any(var in os.environ for var in _AGENT_ENV_VARS)


def session_role() -> str | None:
    """The role stamped into this session's environment, or None when unset/empty."""
    return os.environ.get(ROLE_ENV_VAR) or None


def ai_session() -> bool:
    """True when this invocation is inside an AI session, by either signal: the harness's
    own marker (:func:`running_under_ai_agent`) or a chimera role stamp — a launcher only
    ever stamps :data:`ROLE_ENV_VAR` into sessions it launches, so a non-empty role means
    an AI session even under a future harness that sets no marker of its own."""
    return running_under_ai_agent() or session_role() is not None


def role_scope() -> str | None:
    """The scope the session's role is fenced to, or None when unset/empty."""
    return os.environ.get(ROLE_SCOPE_ENV_VAR) or None


class CrossScopeError(UserError):
    """A project-scoped action resolved a project outside the session's fence.

    *Signpost depth, never privilege*: the message states identity and escalates — it
    never narrates the prevented operation or a flag that would permit it.
    """

    def __init__(self, fenced: str) -> None:
        super().__init__(f'scoped to {fenced}; ask the captain')


def role_scope_for(project: str, goal: str | None = None) -> str:
    """The scope token a launcher stamps beside a role: ``<project>`` for a manager,
    ``<project>@<goal>`` for a goal's agent. Deliberately the session-name grammar
    (``chimera.worktrees.SEP``) — :func:`fenced_project` is the parse side of the pair,
    splitting on the same ``@``."""
    return project if goal is None else f'{project}{SEP}{goal}'


def fenced_project() -> str | None:
    """The project this session's actions are fenced to, or ``None`` when unfenced.

    Arms for a scoped manager; the agent — though its stripped tree carries no ``-p``
    anywhere today — is fenced identically for symmetry (its scope's first ``@`` segment
    names the project), so a command later joining its tree can't quietly widen it.
    The captain, and any unstamped or unscoped session, is unfenced.
    """
    if session_role() not in (ROLE_MANAGER, ROLE_AGENT):
        return None
    scope = role_scope()
    return scope.split(SEP)[0] if scope is not None else None


def refuse_cross_scope(resolved: str) -> None:
    """Refuse when project ``resolved`` falls outside the session's fence; else a no-op.

    Called with the project an action *resolved* — so an explicit cross-scope ``-p`` and
    a cwd standing in another project refuse identically. Listers are never fenced:
    cross-project listing is knowledge, not capability.
    """
    fenced = fenced_project()
    if fenced is not None and resolved != fenced:
        raise CrossScopeError(fenced)


def role_env(role: str, scope: str | None = None) -> dict[str, str]:
    """The env overlay a launcher stamps into a session: its role, plus the scope it is
    fenced to. The captain gets no scope (unfenced); a manager's is ``<project>``, an
    agent's ``<project>@<goal>`` (session-name grammar — it splits on the same ``@``).
    An unscoped stamp writes ``''`` (which :func:`role_scope` reads as unset) rather than
    omitting the variable — the overlay must *clear* a stale scope inherited from the
    parent environment (e.g. a shell inside an agent session launching the captain), or
    the deliberately-unfenced session would report itself fenced."""
    return {ROLE_ENV_VAR: role, ROLE_SCOPE_ENV_VAR: scope if scope is not None else ''}
