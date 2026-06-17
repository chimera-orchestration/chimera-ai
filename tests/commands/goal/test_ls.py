import os
from pathlib import Path

from giterator.testing import Repo
from testfixtures import Command, Replacer, TempDir, compare

from chimera.commands.goal.ls import goals_in_scope
from chimera.context import Scope, resolve_project


def _workspace(tmpdir: TempDir) -> Path:
    ws = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    return ws


def _project(tmpdir: TempDir, ws: Path, name: str, *goals: str) -> Path:
    project = ws / name
    tmpdir.dump(
        str(project.relative_to(tmpdir.path) / 'config.yaml'),
        {'kind': 'project', 'repo': str(project)},
    )
    for goal in goals:
        (project / 'worktrees' / f'{goal}@agent').mkdir(parents=True)
    return project


def test_goals_in_scope_lists_every_project_when_widened(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    _project(tmpdir, ws, 'alpha', 'y', 'x')
    _project(tmpdir, ws, 'beta', 'z')
    compare(
        goals_in_scope(Scope(ws, None, None)),
        expected=[('alpha', 'x'), ('alpha', 'y'), ('beta', 'z')],
    )


def test_goals_in_scope_single_project_when_pinned(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    project = resolve_project(_project(tmpdir, ws, 'alpha', 'y', 'x'))
    _project(tmpdir, ws, 'beta', 'z')
    compare(goals_in_scope(Scope(ws, project, None)), expected=[('alpha', 'x'), ('alpha', 'y')])


def test_goal_ls_cli_prints_bare_names_inside_a_project(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _workspace(tmpdir)
    project = _project(tmpdir, ws, 'alpha', 'y', 'x')
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    os.chdir(project)  # standing in the project → bare goal names
    command.run('goal', 'ls').check(output='x\ny', logging=[('INFO', 'goal ls')])


def test_goal_ls_cli_qualifies_names_when_widened(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _workspace(tmpdir)
    _project(tmpdir, ws, 'alpha', 'x')
    _project(tmpdir, ws, 'beta', 'z')
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    command.run('goal', 'ls').check(output='alpha  x\nbeta  z', logging=[('INFO', 'goal ls')])


def test_goal_ls_cli_reflects_real_worktrees(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _workspace(tmpdir)
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    project = ws / 'proj'
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(repo.path)})
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    os.chdir(project)  # worktree add + goal ls both infer the project from cwd
    command.run('worktree', 'add', 'alpha')
    command.run('worktree', 'add', 'beta')
    command.run('goal', 'ls').check(output='alpha\nbeta', logging=[('INFO', 'goal ls')])
