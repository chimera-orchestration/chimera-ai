from pathlib import Path

from chimera.context import iter_projects


def projects(workspace: Path) -> list[str]:
    """Names of the tracked projects in the workspace, sorted."""
    return [project.name for project in iter_projects(workspace)]
