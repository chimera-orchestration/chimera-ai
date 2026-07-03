import os
from pathlib import Path

from giterator.testing import Repo
from testfixtures import TempDir, compare

from chimera.commands.goal.ls import goals_in_scope
from chimera.context import Scope, resolve_project
from tests.cli import Command, action_logs


def _project(tmpdir: TempDir, ws: Path, name: str, *goals: str) -> Path:
    project = ws / name
    tmpdir.dump(
        project / 'config.yaml',
        {'kind': 'project', 'repo': str(project)},
    )
    for goal in goals:
        (project / 'worktrees' / f'{goal}@agent').mkdir(parents=True)
    return project


def test_goals_in_scope_lists_every_project_when_widened(tmpdir: TempDir, workspace: Path) -> None:
    _project(tmpdir, workspace, 'alpha', 'y', 'x')
    _project(tmpdir, workspace, 'beta', 'z')
    compare(
        goals_in_scope(Scope(workspace, None, None)),
        expected=[('alpha', 'x'), ('alpha', 'y'), ('beta', 'z')],
    )


def test_goals_in_scope_single_project_when_pinned(tmpdir: TempDir, workspace: Path) -> None:
    project = resolve_project(_project(tmpdir, workspace, 'alpha', 'y', 'x'))
    _project(tmpdir, workspace, 'beta', 'z')  # a second project that must not appear
    compare(
        goals_in_scope(Scope(workspace, project, None)), expected=[('alpha', 'x'), ('alpha', 'y')]
    )


def test_goal_ls_cli_prints_bare_names_inside_a_project(
    tmpdir: TempDir, workspace_with_env: Path, command: Command
) -> None:
    project = _project(tmpdir, workspace_with_env, 'alpha', 'y', 'x')
    os.chdir(project)  # standing in the project → bare goal names
    command.run('goal', 'ls').check(
        output='x\ny',
        logging=action_logs(
            'goal ls', 'chimera.commands.goal.ls.goals_in_scope', {'project': None}
        ),
    )


def test_goal_ls_cli_qualifies_names_when_widened(
    tmpdir: TempDir, workspace_with_env: Path, command: Command
) -> None:
    _project(tmpdir, workspace_with_env, 'alpha', 'x')
    _project(tmpdir, workspace_with_env, 'beta', 'z')
    command.run('goal', 'ls').check(
        output='alpha  x\nbeta  z',
        logging=action_logs(
            'goal ls', 'chimera.commands.goal.ls.goals_in_scope', {'project': None}
        ),
    )


def test_goal_ls_cli_reflects_real_worktrees(
    tmpdir: TempDir, git_repo: Repo, workspace_with_env: Path, command: Command
) -> None:
    project = workspace_with_env / 'proj'
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    os.chdir(project)  # worktree add + goal ls both infer the project from cwd
    command.run('worktree', 'add', '--goal', 'alpha')
    command.run('worktree', 'add', '--goal', 'beta')
    command.run('goal', 'ls').check(
        output='alpha\nbeta',
        logging=action_logs(
            'goal ls', 'chimera.commands.goal.ls.goals_in_scope', {'project': None}
        ),
    )
