from pathlib import Path

import pytest
from testfixtures import TempDir
from typer.testing import CliRunner

from chimera.__main__ import app
from chimera.commands.agent import Agent
from chimera.commands.ls import Board, GoalBoard, ProjectBoard, board
from chimera.context import Scope, resolve_project

runner = CliRunner()


def _workspace(tmpdir: TempDir) -> Path:
    ws = tmpdir.makedir('lycia')
    (ws / 'config.yaml').write_text('kind: workspace\n')
    return ws


def _project(ws: Path, name: str, *goals: str) -> Path:
    project = ws / name
    project.mkdir()
    (project / 'config.yaml').write_text(f'kind: project\nrepo: {project}\n')
    for goal in goals:
        (project / 'worktrees' / f'{goal}@agent').mkdir(parents=True)
    return project


def _agent(
    cwd: Path, name: str, status: str = 'idle', summary: str | None = None, id: str = 'id'
) -> Agent:
    return Agent(id, name, status, cwd, summary)


def test_board_partitions_agents_into_goals_project_loose_and_workspace_loose(
    tmpdir: TempDir,
) -> None:
    ws = _workspace(tmpdir)
    _project(ws, 'alpha', 'g')
    in_goal = _agent(ws / 'alpha' / 'worktrees' / 'g@agent', 'alpha@g@agent', 'busy')
    in_repo = _agent(ws / 'alpha' / 'repo', 'loose-proj')  # under the project, not a goal
    stray = _agent(ws / 'scratch', 'stray-ws')  # under the workspace, not a project
    outside = _agent(tmpdir.path / 'elsewhere', 'outside')  # filtered out entirely
    result = board(Scope(ws, None, None), [in_goal, in_repo, stray, outside])
    assert result == Board(
        workspace='lycia',
        projects=[ProjectBoard('alpha', [GoalBoard('g', [in_goal])], [in_repo])],
        loose=[stray],
    )


def test_board_pinned_goal_shows_only_that_goal(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    _project(ws, 'alpha', 'g', 'other')
    project = resolve_project(ws / 'alpha')
    a = _agent(ws / 'alpha' / 'worktrees' / 'g@agent', 'a')
    b = _agent(ws / 'alpha' / 'worktrees' / 'other@agent', 'b')
    result = board(Scope(ws, project, 'g'), [a, b])
    assert result == Board('lycia', [ProjectBoard('alpha', [GoalBoard('g', [a])], [])], [])


def _cli_workspace(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = _workspace(tmpdir)
    monkeypatch.setenv('CHIMERA_WORKSPACE', str(ws))
    monkeypatch.chdir(ws)
    return ws


def test_ls_cli_renders_the_tree(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _cli_workspace(tmpdir, monkeypatch)
    _project(ws, 'alpha', 'g')
    worktree = ws / 'alpha' / 'worktrees' / 'g@agent'
    monkeypatch.setattr(
        'chimera.__main__.agents',
        lambda: [Agent('012a9550', 'alpha@g@agent', 'busy', worktree, 'fix the bug')],
    )
    result = runner.invoke(app, ['ls'])
    assert result.exit_code == 0
    assert result.output.splitlines() == [
        'lycia',
        '  alpha',
        '    g',
        '      012a9550  alpha@g@agent  busy  fix the bug',
    ]


def test_ls_cli_renders_loose_agents(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _cli_workspace(tmpdir, monkeypatch)
    _project(ws, 'alpha')  # no goals; a session in repo/ is project-loose
    stray = ws / 'scratch'  # under the workspace but no project → board-loose
    monkeypatch.setattr(
        'chimera.__main__.agents',
        lambda: [
            Agent('012a9550', 'repo-sess', 'busy', ws / 'alpha' / 'repo', 'building'),
            Agent('39d68dfa', 'stray', 'idle', stray, None),
        ],
    )
    result = runner.invoke(app, ['ls'])
    assert result.exit_code == 0
    assert result.output.splitlines() == [
        'lycia',
        '  alpha',
        '    · 012a9550  repo-sess  busy  building',
        f'  · 39d68dfa  stray  idle  {stray}',
    ]


def test_ls_cli_marks_empty_goals_and_projects(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _cli_workspace(tmpdir, monkeypatch)
    _project(ws, 'alpha', 'g')
    _project(ws, 'beta')
    monkeypatch.setattr('chimera.__main__.agents', list)
    result = runner.invoke(app, ['ls'])
    assert result.exit_code == 0
    assert result.output.splitlines() == [
        'lycia',
        '  alpha',
        '    g  (no agent)',
        '  beta',
        '    (no goals)',
    ]
