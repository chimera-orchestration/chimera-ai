from pathlib import Path

from testfixtures import ShouldRaise, TempDir, compare

from chimera.config import (
    NotInProjectError,
    NotInWorkspaceError,
    ProjectConfig,
    WorkspaceConfig,
    find_project,
    find_workspace,
    load_config,
)


def _project(tmpdir: TempDir, parent, name: str = 'proj', repo: str = '/some/repo'):
    project = parent / name
    tmpdir.dump(
        str(project.relative_to(tmpdir.path) / 'config.yaml'), {'kind': 'project', 'repo': repo}
    )
    return project


def test_load_config_workspace(workspace: Path) -> None:
    compare(load_config(workspace), expected=WorkspaceConfig(kind='workspace'))


def test_load_config_project(tmpdir: TempDir) -> None:
    project = _project(tmpdir, tmpdir.path, repo='/r')
    compare(load_config(project), expected=ProjectConfig(kind='project', repo=Path('/r')))


def test_load_config_absent(tmpdir: TempDir) -> None:
    assert load_config(tmpdir.path) is None


def test_find_workspace_at_start(workspace: Path) -> None:
    compare(find_workspace(workspace), expected=workspace)


def test_find_workspace_from_nested_project(tmpdir: TempDir, workspace: Path) -> None:
    project = _project(tmpdir, workspace)
    compare(find_workspace(project), expected=workspace)  # walks up past the project config


def test_find_workspace_raises_outside(tmpdir: TempDir) -> None:
    with ShouldRaise(NotInWorkspaceError(tmpdir.path)):
        find_workspace(tmpdir.path)


def test_find_project_at_start(tmpdir: TempDir, workspace: Path) -> None:
    project = _project(tmpdir, workspace, repo='/r')
    compare(
        find_project(project), expected=(project, ProjectConfig(kind='project', repo=Path('/r')))
    )


def test_find_project_raises_in_a_bare_workspace(workspace: Path) -> None:
    with ShouldRaise(NotInProjectError(workspace)):  # workspace config is not a project
        find_project(workspace)
