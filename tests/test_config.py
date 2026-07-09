from pathlib import Path

from testfixtures import ShouldRaise, TempDir, compare

from chimera.config import (
    AgentConfig,
    CaptainConfig,
    NotInProjectError,
    NotInWorkspaceError,
    ProjectConfig,
    WorkspaceConfig,
    find_project,
    find_workspace,
    load_config,
    workspace_config,
)


def _project(tmpdir: TempDir, parent, name: str = 'proj', repo: str = '/some/repo'):
    project = parent / name
    tmpdir.dump(project / 'config.yaml', {'kind': 'project', 'repo': repo})
    return project


def test_load_config_workspace(workspace: Path) -> None:
    compare(load_config(workspace), expected=WorkspaceConfig(kind='workspace'))


def test_load_config_project(tmpdir: TempDir) -> None:
    project = _project(tmpdir, tmpdir.path, repo='/r')
    compare(load_config(project), expected=ProjectConfig(kind='project', repo=Path('/r')))


def test_load_config_absent(tmpdir: TempDir) -> None:
    assert load_config(tmpdir.path) is None


def test_load_config_agent_cascade_levels(tmpdir: TempDir) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace', 'agent': {'harness': 'claude'}})
    project = tmpdir.path / 'ws' / 'proj'
    tmpdir.dump(
        project / 'config.yaml', {'kind': 'project', 'repo': '/r', 'agent': {'model': 'opus'}}
    )
    compare(
        load_config(tmpdir.path / 'ws'),
        expected=WorkspaceConfig(kind='workspace', agent=AgentConfig(harness='claude')),
    )
    compare(
        load_config(project),
        expected=ProjectConfig(kind='project', repo=Path('/r'), agent=AgentConfig(model='opus')),
    )


def test_workspace_config_parses_the_root(workspace: Path) -> None:
    compare(workspace_config(workspace), expected=WorkspaceConfig(kind='workspace'))


def test_workspace_config_rejects_a_non_workspace(tmpdir: TempDir) -> None:
    with ShouldRaise(NotInWorkspaceError(tmpdir.path)):
        workspace_config(tmpdir.path)


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


def test_captain_string_shorthand(tmpdir: TempDir) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace', 'captain': 'pegasus'})
    compare(
        load_config(tmpdir.path / 'ws'),
        expected=WorkspaceConfig(kind='workspace', captain=CaptainConfig(name='pegasus')),
    )


def test_captain_full_form_with_agent_overrides(tmpdir: TempDir) -> None:
    tmpdir.dump(
        'ws/config.yaml',
        {'kind': 'workspace', 'captain': {'name': 'pegasus', 'model': 'opus'}},
    )
    compare(
        load_config(tmpdir.path / 'ws'),
        expected=WorkspaceConfig(
            kind='workspace', captain=CaptainConfig(name='pegasus', model='opus')
        ),
    )


def test_captain_defaults_to_plain_captain(workspace: Path) -> None:
    compare(workspace_config(workspace).captain, expected=CaptainConfig(name='captain'))
