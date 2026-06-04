from pathlib import Path

import yaml

_PROJECT_DIRS = ('knowledge', 'prompts', 'principles', 'processes')


def track(workspace: Path, repo: Path) -> Path:
    """Register an existing checkout as a project in the workspace; return the project dir."""
    repo = repo.resolve()
    if not repo.is_dir():
        raise NotADirectoryError(repo)
    project = workspace / repo.name
    for sub in _PROJECT_DIRS:
        (project / sub).mkdir(parents=True, exist_ok=True)
    (project / 'config.yaml').write_text(yaml.safe_dump({'repo': str(repo)}))
    return project
