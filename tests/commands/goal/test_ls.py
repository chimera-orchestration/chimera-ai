from pathlib import Path

import pytest
from giterator.testing import Repo
from testfixtures import Replacer, TempDir
from typer.testing import CliRunner

from chimera.__main__ import app
from chimera.commands.goal.ls import goals_in_scope
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


def test_goals_in_scope_lists_every_project_when_widened(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    _project(ws, 'alpha', 'y', 'x')
    _project(ws, 'beta', 'z')
    assert goals_in_scope(Scope(ws, None, None)) == [('alpha', 'x'), ('alpha', 'y'), ('beta', 'z')]


def test_goals_in_scope_single_project_when_pinned(tmpdir: TempDir) -> None:
    ws = _workspace(tmpdir)
    project = resolve_project(_project(ws, 'alpha', 'y', 'x'))
    _project(ws, 'beta', 'z')
    assert goals_in_scope(Scope(ws, project, None)) == [('alpha', 'x'), ('alpha', 'y')]


def test_goal_ls_cli_prints_bare_names_inside_a_project(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch, replace: Replacer
) -> None:
    ws = _workspace(tmpdir)
    project = _project(ws, 'alpha', 'y', 'x')
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    monkeypatch.chdir(project)
    result = runner.invoke(app, ['goal', 'ls'])
    assert result.exit_code == 0
    assert result.output.splitlines() == ['x', 'y']


def test_goal_ls_cli_qualifies_names_when_widened(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch, replace: Replacer
) -> None:
    ws = _workspace(tmpdir)
    _project(ws, 'alpha', 'x')
    _project(ws, 'beta', 'z')
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    monkeypatch.chdir(ws)
    result = runner.invoke(app, ['goal', 'ls'])
    assert result.exit_code == 0
    assert result.output.splitlines() == ['alpha  x', 'beta  z']


def test_goal_ls_cli_reflects_real_worktrees(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch, replace: Replacer
) -> None:
    ws = _workspace(tmpdir)
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    project = ws / 'proj'
    project.mkdir()
    (project / 'config.yaml').write_text(f'kind: project\nrepo: {repo.path}\n')
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    monkeypatch.chdir(project)
    runner.invoke(app, ['worktree', 'add', 'alpha'])
    runner.invoke(app, ['worktree', 'add', 'beta'])
    result = runner.invoke(app, ['goal', 'ls'])
    assert result.exit_code == 0
    assert result.output.split() == ['alpha', 'beta']
