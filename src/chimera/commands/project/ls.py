from pathlib import Path

from chimera.config import ProjectConfig, load_config


def projects(workspace: Path) -> list[str]:
    """Names of the tracked projects in the workspace, sorted."""
    return sorted(
        child.name
        for child in workspace.iterdir()
        if child.is_dir() and isinstance(load_config(child), ProjectConfig)
    )
