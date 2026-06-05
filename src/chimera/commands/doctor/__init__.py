from collections.abc import Sequence
from pathlib import Path

from chimera.commands.doctor.checks import CHECKS
from chimera.commands.doctor.core import Check, Finding
from chimera.config import NotInWorkspaceError

__all__ = ['CHECKS', 'Check', 'Finding', 'doctor']


def doctor(workspace: Path, fix: bool = False, checks: Sequence[Check] = CHECKS) -> list[Finding]:
    """Run every check over the workspace, optionally fixing; return the findings.

    Treats workspace as the root (no walking up — the root marker may be the very
    thing that's missing). Raises if it shows no sign of being a workspace at all.
    """
    if not _looks_like_workspace(workspace):
        raise NotInWorkspaceError(workspace)
    findings: list[Finding] = []
    for check in checks:
        findings.extend(check.run(workspace, fix))
    return findings


def _looks_like_workspace(workspace: Path) -> bool:
    return (
        (workspace / 'config.yaml').exists()
        or (workspace / '.beads').is_dir()
        or (workspace / 'processes').is_dir()
    )
