from pathlib import Path

import pytest
from testfixtures import TempDir, compare

from chimera.config import (
    NotInProjectError,
    NotInWorkspaceError,
    ProjectConfig,
    WorkspaceConfig,
    find_project,
    find_workspace,
    load_config,
)


def _workspace(tmpdir: TempDir, name: str = 'lycia'):
    ws = tmpdir.makedir(name)
    (ws / 'config.yaml').write_text('kind: workspace\n')
    return ws


def _project(parent, name: str = 'proj', repo: str = '/some/repo'):
    project = parent / name
    project.mkdir()
    (project / 'config.yaml').write_text(f'kind: project\nrepo: {repo}\n')
    return project


def test_load_config_workspace(tmpdir: TempDir) -> None:
    compare(load_config(_workspace(tmpdir)), expected=WorkspaceConfig(kind='workspace'))


def test_load_config_project(tmpdir: TempDir) -> None:
    project = _project(tmpdir.path, repo='/r')
    compare(load_config(project), expected=ProjectConfig(kind='project', repo=Path('/r')))


def test_load_config_absent(tmpdir: TempDir) -> None:
    assert load_config(tmpdir.path) is None


def test_find_workspace_at_start(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    compare(find_workspace(ws), expected=ws)


def test_find_workspace_from_nested_project(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    project = _project(ws)
    compare(find_workspace(project), expected=ws)  # walks up past the project config


def test_find_workspace_raises_outside(tmpdir: TempDir) -> None:
    with pytest.raises(NotInWorkspaceError):
        find_workspace(tmpdir.path)


def test_find_project_at_start(tmpdir: TempDir) -> None:
    project = _project(_workspace(tmpdir), repo='/r')
    compare(
        find_project(project), expected=(project, ProjectConfig(kind='project', repo=Path('/r')))
    )


def test_find_project_raises_in_a_bare_workspace(tmpdir: TempDir) -> None:
    with pytest.raises(NotInProjectError):
        find_project(_workspace(tmpdir))  # workspace config is not a project
