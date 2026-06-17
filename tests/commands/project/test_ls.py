from testfixtures import Command, TempDir, compare

from chimera.commands.project.ls import projects


def _workspace_with_projects(tmpdir: TempDir) -> TempDir:
    tmpdir.dump('config.yaml', {'kind': 'workspace'})
    for name in ('beta', 'alpha'):
        tmpdir.dump(f'{name}/config.yaml', {'kind': 'project', 'repo': f'/r/{name}'})
    tmpdir.makedir('not-a-project')  # no config.yaml — ignored
    return tmpdir


def test_projects_lists_tracked_projects_sorted(tmpdir: TempDir) -> None:
    _workspace_with_projects(tmpdir)
    compare(projects(tmpdir.path), expected=['alpha', 'beta'])


def test_project_ls_cli(tmpdir: TempDir, command: Command) -> None:
    _workspace_with_projects(tmpdir)
    command.run('project', 'ls').check(output='alpha\nbeta', logging=[('INFO', 'project ls')])
