"""Detects whether chimera is being invoked by an AI coding agent rather than a human
at an interactive terminal. Deliberately the only place that reads the process
environment for this purpose, so a future harness-agnostic detector is a one-file change.
"""

import os
from pathlib import Path

from loguru import logger

from chimera.addresses import Address, AnyAddress, Actor, Captain, Manager
from chimera.config import UserError
from chimera.identity import current_session

_AGENT_ENV_VARS = ('CLAUDECODE',)  # Claude Code sets this for every subprocess it launches

RESTRICTED_OPTIONS = frozenset({'--force', '--dangerous'})

# Human-only commands (canonical leaf paths): stripped from every AI session's tree — the
# captain included, unlike the per-role allowlists — just as RESTRICTED_OPTIONS strips
# options. `logtail` is the human's live debugging surface: it blocks following the log
# until Ctrl-C, a dead end for an agent, which reads the JSONL directly instead.
# `dashboard` is `ls`'s colorized/columnar twin for a human terminal — the ANSI codes are
# noise for an agent parsing text, so it reads `ch ls` instead (same underlying board()).
# `prompt edit` blocks on $EDITOR, the same dead end as logtail's follow; an agent that
# wants a project template writes the file its own way after `ch prompt init`.
RESTRICTED_COMMANDS = frozenset({'logtail', 'dashboard', 'prompt edit'})

# The session-layer roles: the workspace captain, a project's manager (its chat), and a
# goal's agent. ROLE_-prefixed to avoid colliding with chimera.worktrees.AGENT — the same
# string on a different axis (actor naming vs session role), deliberately kept apart.
ROLE_CAPTAIN, ROLE_MANAGER, ROLE_AGENT = 'captain', 'manager', 'agent'

_ROLES: dict[type[Address], str] = {
    Captain: ROLE_CAPTAIN,
    Manager: ROLE_MANAGER,
    Actor: ROLE_AGENT,
}
"""The address shapes *are* the roles — one mapping, so the two can never drift."""

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

# Looking at sessions is knowledge, not capability — the same rule that leaves the listers
# unfenced. `whoami` especially: a session that can't ask what it is has to guess, which is
# the whole failure this fence exists to prevent.
_SESSION_COMMANDS = frozenset({'session whoami', 'session show'})

# The harness hooks — installed user-wide, so they fire inside chimera-launched sessions too,
# where the role strip would otherwise reach them. They record to the archive and deliver
# mail; harmless as capability, and the hook process (not the agent) is what invokes them.
_HOOK_COMMANDS = frozenset({'hook session-start', 'hook session-end', 'hook deliver'})

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
            'dump',
            # the templates review and goal pr render are the manager's to tune for its
            # project; `prompt edit` stays human-only (RESTRICTED_COMMANDS, above)
            'prompt ls',
            'prompt show',
            'prompt init',
        }
    )
    | _MSG_COMMANDS
    | _HOOK_COMMANDS
    | _SESSION_COMMANDS,
    # errand is deliberately in both trees: cross-project *reading* is knowledge, not
    # capability (same rule that leaves listers unfenced), and its target axis has its
    # own containment (see __main__._foreign)
    ROLE_AGENT: frozenset({'help', 'prime', 'errand', 'dump'})
    | _MSG_COMMANDS
    | _HOOK_COMMANDS
    | _SESSION_COMMANDS,
}


def running_under_ai_agent() -> bool:
    """True when the current process was launched by (or under) an AI coding agent."""
    return any(var in os.environ for var in _AGENT_ENV_VARS)


def session_address(cwd: Path) -> AnyAddress | None:
    """The address of the session running this command, parsed, or ``None``.

    Where the role and its fence both come from. An address already encodes both — a
    manager's names its project, an agent's names project and goal — so nothing has to
    be stamped anywhere or kept in step with anything.

    This is why the role stamp is gone. ``CHIMERA_ROLE`` was written into the launched
    session's environment, which reaches a foreground session and nothing else: a
    background launch runs in a pooled worker that never sees it, and a bridge to the
    background mints a fresh process the same way. Passing a prompt is exactly what makes
    a launch background, so the unattended agents — the ones the fence most exists for —
    were precisely the ones it never reached. The address survives all three, because it
    lives in the archive rather than in an environment.

    An address the grammar can't parse is treated as no address at all: an unparseable
    claim is not a claim.
    """
    session = current_session(cwd)
    if session is None or session.address is None:
        return None
    try:
        return Address.parse(session.address)
    except ValueError:
        logger.bind(address=session.address, native_id=session.native_id).warning(
            'agent: session address does not parse, treating the session as unaddressed'
        )
        return None


def session_role(cwd: Path) -> str | None:
    """The role of the session running this command, or ``None`` for a human.

    Read off the address (:func:`session_address`), whose three shapes *are* the three
    roles — so a role can't disagree with the address that names it.
    """
    address = session_address(cwd)
    return None if address is None else _ROLES[type(address)]


def ai_session(cwd: Path) -> bool:
    """True when this invocation is inside an AI session, by either signal: the harness's
    own marker (:func:`running_under_ai_agent`) or the archive knowing this process to be
    a session it recorded — so a future harness that sets no marker of its own is still
    caught, provided chimera launched it."""
    return running_under_ai_agent() or current_session(cwd) is not None


class CrossScopeError(UserError):
    """A project-scoped action resolved a project outside the session's fence.

    *Signpost depth, never privilege*: the message states identity and escalates — it
    never narrates the prevented operation or a flag that would permit it.
    """

    def __init__(self, fenced: str) -> None:
        super().__init__(f'scoped to {fenced}; ask the captain')


def fenced_project(cwd: Path) -> str | None:
    """The project this session's actions are fenced to, or ``None`` when unfenced.

    The address names it: a manager's is its project, an agent's the project its goal
    belongs to. The agent's tree carries no ``-p`` anywhere today, but it is fenced
    identically for symmetry, so a command later joining that tree can't quietly widen
    it. The captain is unfenced by construction — its address names no project — and so
    is a human.
    """
    address = session_address(cwd)
    return address.project or None if address is not None else None


def refuse_cross_scope(cwd: Path, resolved: str) -> None:
    """Refuse when project ``resolved`` falls outside the session's fence; else a no-op.

    Called with the project an action *resolved* — so an explicit cross-scope ``-p`` and
    a cwd standing in another project refuse identically. Listers are never fenced:
    cross-project listing is knowledge, not capability.
    """
    fenced = fenced_project(cwd)
    if fenced is not None and resolved != fenced:
        raise CrossScopeError(fenced)
