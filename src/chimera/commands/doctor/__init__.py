from collections.abc import Sequence
from pathlib import Path

from chimera.commands.doctor.checks import CHECKS
from chimera.commands.doctor.core import Check, Finding, read_raw
from chimera.config import NotInWorkspaceError

__all__ = ['CHECKS', 'Check', 'Finding', 'doctor', 'find_workspace_root', 'resolve_root']


def doctor(workspace: Path, fix: bool = False, checks: Sequence[Check] = CHECKS) -> list[Finding]:
    """Run every check over the workspace root, optionally fixing; return the findings.

    Trusts workspace to be the root — resolve it with find_workspace_root first.
    """
    findings: list[Finding] = []
    for check in checks:
        findings.extend(check.run(workspace, fix))
    return findings


def resolve_root(path: Path | None, cwd: Path, env: str | None) -> Path:
    """The workspace root doctor should check, by precedence.

    An explicit ``path`` wins; else ``$CHIMERA_WORKSPACE`` (trusted as given — doctor's
    job is to repair a misconfigured root, so it doesn't re-validate it); else walk up
    from ``cwd``. This mirrors ``chimera.context.resolve_workspace`` so doctor agrees
    with every other command about which workspace it's looking at.
    """
    if path is not None:
        return find_workspace_root(path.resolve())
    if env:
        return Path(env).expanduser().resolve()
    return find_workspace_root(cwd.resolve())


def find_workspace_root(start: Path) -> Path:
    """Walk up from start to the nearest workspace root; raise if there is none.

    A directory whose config.yaml carries ``repo:`` is a project, never the root, so
    it is skipped — this is what stops doctor mistaking a project dir (which also has
    ``processes/`` etc.) for a workspace and corrupting its config.
    """
    for directory in (start, *start.parents):
        if _is_workspace_root(directory):
            return directory
    raise NotInWorkspaceError(start)


def _is_workspace_root(directory: Path) -> bool:
    raw = read_raw(directory)
    if raw and 'repo' in raw:
        return False  # a project, not the workspace root
    if raw and raw.get('kind') == 'workspace':
        return True
    # legacy root: shows workspace evidence and isn't a project
    return (directory / '.beads').is_dir() or (directory / 'processes').is_dir()
