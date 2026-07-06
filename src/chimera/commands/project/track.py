from pathlib import Path

import yaml

from chimera.config import ProjectConfig

_PROJECT_DIRS = ('knowledge', 'prompts', 'principles', 'processes')


def track(workspace: Path, repo: Path) -> Path:
    """Register an existing checkout as a project in the workspace; return the project dir."""
    repo = repo.resolve()
    if not repo.exists():
        raise FileNotFoundError(repo)
    if not repo.is_dir():
        raise NotADirectoryError(repo)
    return register(workspace, repo.name, repo)


def register(workspace: Path, name: str, repo: Path) -> Path:
    """Scaffold project dir <name> and write its config pointing at repo; return the dir."""
    project = workspace / name
    for sub in _PROJECT_DIRS:
        (project / sub).mkdir(parents=True, exist_ok=True)
    config = ProjectConfig(kind='project', repo=repo)
    # exclude_defaults: an unset cascade level (agent:) stays out of freshly-written config
    (project / 'config.yaml').write_text(
        yaml.safe_dump(config.model_dump(mode='json', exclude_defaults=True))
    )
    return project
