import os
from pathlib import Path

from chimera.agent_env import ROLE_MANAGER
from chimera.comms import Comms
from chimera.config import workspace_config
from chimera.context import resolve_scope
from chimera.worktrees import AGENT, SEP, session_name


def mail(workspace: Path) -> Comms:
    """The workspace's mail store, rooted at ``state/mail``."""
    return Comms(workspace / 'state' / 'mail')


def caller(cwd: Path) -> str:
    """The address of whoever is running ``ch msg`` here — the sender/inbox default.

    ``CHIMERA_SESSION`` if the launcher stamped it, else inferred from cwd like the listers:
    the bare workspace → the captain's persona name, a project dir → ``<project>@manager``, a
    goal worktree → ``<project>@<goal>@agent``. A non-agent actor on a goal (a reviewer, a
    human) names itself with ``--from`` instead.
    """
    if stamped := os.environ.get('CHIMERA_SESSION'):
        return stamped
    scope = resolve_scope(cwd)
    if scope.project is None:
        return workspace_config(scope.workspace).captain.name
    if scope.goal is None:
        return f'{scope.project.name}{SEP}{ROLE_MANAGER}'
    return session_name(scope.project.name, scope.goal, AGENT)
