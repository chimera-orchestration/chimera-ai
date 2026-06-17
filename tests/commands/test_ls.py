import os
from pathlib import Path

from testfixtures import Command, Replacer, TempDir, compare

from chimera import __main__ as chimera_main
from chimera.commands.agent import Agent, agents
from chimera.commands.ls import Board, GoalBoard, ProjectBoard, board
from chimera.context import Scope, resolve_project


def _project(tmpdir: TempDir, ws: Path, name: str, *goals: str) -> Path:
    project = ws / name
    tmpdir.dump(
        str(project.relative_to(tmpdir.path) / 'config.yaml'),
        {'kind': 'project', 'repo': str(project)},
    )
    for goal in goals:
        (project / 'worktrees' / f'{goal}@agent').mkdir(parents=True)
    return project


def _agent(
    cwd: Path, name: str, status: str = 'idle', summary: str | None = None, id: str = 'id'
) -> Agent:
    return Agent(id, name, status, cwd, summary)


def test_board_partitions_agents_into_goals_project_loose_and_workspace_loose(
    tmpdir: TempDir, workspace: Path
) -> None:
    _project(tmpdir, workspace, 'alpha', 'g')
    in_goal = _agent(workspace / 'alpha' / 'worktrees' / 'g@agent', 'alpha@g@agent', 'busy')
    in_repo = _agent(workspace / 'alpha' / 'repo', 'loose-proj')  # under the project, not a goal
    stray = _agent(workspace / 'scratch', 'stray-ws')  # under the workspace, not a project
    outside = _agent(tmpdir.path / 'elsewhere', 'outside')  # filtered out entirely
    result = board(Scope(workspace, None, None), [in_goal, in_repo, stray, outside])
    compare(
        result,
        expected=Board(
            workspace='lycia',
            projects=[ProjectBoard('alpha', [GoalBoard('g', [in_goal])], [in_repo])],
            loose=[stray],
        ),
    )


def test_board_pinned_goal_shows_only_that_goal(tmpdir: TempDir, workspace: Path) -> None:
    _project(tmpdir, workspace, 'alpha', 'g', 'other')
    project = resolve_project(workspace / 'alpha')
    a = _agent(workspace / 'alpha' / 'worktrees' / 'g@agent', 'a')
    b = _agent(workspace / 'alpha' / 'worktrees' / 'other@agent', 'b')
    result = board(Scope(workspace, project, 'g'), [a, b])
    compare(result, expected=Board('lycia', [ProjectBoard('alpha', [GoalBoard('g', [a])], [])], []))


def test_ls_cli_renders_the_tree(
    tmpdir: TempDir, workspace_with_env: Path, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, workspace_with_env, 'alpha', 'g')
    worktree = workspace_with_env / 'alpha' / 'worktrees' / 'g@agent'
    replace.in_module(
        agents,
        lambda: [Agent('012a9550', 'alpha@g@agent', 'busy', worktree, 'fix the bug')],
        module=chimera_main,
    )
    command.run('ls').check(
        output='\n'.join(
            [
                'lycia',
                '  alpha',
                '    g',
                '      012a9550  alpha@g@agent  busy  fix the bug',
            ]
        ),
        logging=[('INFO', 'ls')],
    )


def test_ls_cli_renders_loose_agents(
    tmpdir: TempDir, workspace_with_env: Path, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, workspace_with_env, 'alpha')  # no goals; a session in repo/ is project-loose
    stray = workspace_with_env / 'scratch'  # under the workspace but no project → board-loose
    replace.in_module(
        agents,
        lambda: [
            Agent(
                '012a9550', 'repo-sess', 'busy', workspace_with_env / 'alpha' / 'repo', 'building'
            ),
            Agent('39d68dfa', 'stray', 'idle', stray, None),
        ],
        module=chimera_main,
    )
    command.run('ls').check(
        output='\n'.join(
            [
                'lycia',
                '  alpha',
                '    · 012a9550  repo-sess  busy  building',
                f'  · 39d68dfa  stray  idle  {stray}',
            ]
        ),
        logging=[('INFO', 'ls')],
    )


def test_ls_cli_stays_global_from_inside_a_project(
    tmpdir: TempDir, workspace_with_env: Path, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, workspace_with_env, 'alpha', 'g')
    _project(tmpdir, workspace_with_env, 'beta')
    os.chdir(workspace_with_env / 'alpha')  # standing in a project must not narrow the dashboard
    replace.in_module(agents, list, module=chimera_main)
    command.run('ls').check(
        output='\n'.join(
            [
                'lycia',
                '  alpha',
                '    g  (no agent)',
                '  beta',
                '    (no goals)',
            ]
        ),
        logging=[('INFO', 'ls')],
    )


def test_ls_cli_marks_empty_goals_and_projects(
    tmpdir: TempDir, workspace_with_env: Path, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, workspace_with_env, 'alpha', 'g')
    _project(tmpdir, workspace_with_env, 'beta')
    replace.in_module(agents, list, module=chimera_main)
    command.run('ls').check(
        output='\n'.join(
            [
                'lycia',
                '  alpha',
                '    g  (no agent)',
                '  beta',
                '    (no goals)',
            ]
        ),
        logging=[('INFO', 'ls')],
    )
