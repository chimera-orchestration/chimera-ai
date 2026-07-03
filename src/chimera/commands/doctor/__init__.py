from collections.abc import Sequence
from pathlib import Path

from chimera.commands.doctor.checks import CHECKS
from chimera.commands.doctor.core import Check, Finding, read_raw
from chimera.config import NotInWorkspaceError, UserError

__all__ = [
    'CHECKS',
    'Check',
    'Finding',
    'UnknownCheckError',
    'doctor',
    'find_workspace_root',
    'resolve_root',
    'select_checks',
]


class UnknownCheckError(UserError):
    def __init__(self, unknown: Sequence[str], valid: Sequence[str]) -> None:
        plural = 's' if len(unknown) != 1 else ''
        super().__init__(
            f'unknown check{plural}: {", ".join(unknown)} (available: {", ".join(valid)})'
        )


def select_checks(names: Sequence[str], checks: Sequence[Check] = CHECKS) -> tuple[Check, ...]:
    """The checks matching names — all of them for no names.

    Always in registry order, whatever order the names came in: the registry's order is
    load-bearing (workspace-clean runs last to sweep up earlier fixes' edits).
    """
    if not names:
        return tuple(checks)
    valid = [check.name for check in checks]
    if unknown := [name for name in names if name not in valid]:
        raise UnknownCheckError(unknown, valid)
    wanted = set(names)
    return tuple(check for check in checks if check.name in wanted)


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
    return (directory / 'processes').is_dir()
