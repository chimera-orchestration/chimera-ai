from pathlib import Path

from testfixtures import Command, TempDir, compare

from chimera.commands.project.ls import projects


def _projects(tmpdir: TempDir, workspace: Path) -> None:
    for name in ('beta', 'alpha'):
        tmpdir.dump(
            workspace / name / 'config.yaml',
            {'kind': 'project', 'repo': f'/r/{name}'},
        )
    (workspace / 'not-a-project').mkdir()  # no config.yaml — ignored


def test_projects_lists_tracked_projects_sorted(tmpdir: TempDir, workspace: Path) -> None:
    _projects(tmpdir, workspace)
    compare(projects(workspace), expected=['alpha', 'beta'])


def test_project_ls_cli(tmpdir: TempDir, workspace_with_env: Path, command: Command) -> None:
    _projects(tmpdir, workspace_with_env)
    command.run('project', 'ls').check(output='alpha\nbeta', logging=[('INFO', 'project ls')])
