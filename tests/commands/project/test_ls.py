from testfixtures import Command, TempDir

from chimera.commands.project.ls import projects


def _workspace_with_projects(tmpdir: TempDir) -> TempDir:
    (tmpdir.path / 'config.yaml').write_text('kind: workspace\n')
    for name in ('beta', 'alpha'):
        project = tmpdir.makedir(name)
        (project / 'config.yaml').write_text(f'kind: project\nrepo: /r/{name}\n')
    tmpdir.makedir('not-a-project')  # no config.yaml — ignored
    return tmpdir


def test_projects_lists_tracked_projects_sorted(tmpdir: TempDir) -> None:
    _workspace_with_projects(tmpdir)
    assert projects(tmpdir.path) == ['alpha', 'beta']


def test_project_ls_cli(tmpdir: TempDir, command: Command) -> None:
    _workspace_with_projects(tmpdir)
    command.run('project', 'ls').check(output='alpha\nbeta', logging=[('INFO', 'project ls')])
