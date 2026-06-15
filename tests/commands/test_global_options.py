import subprocess
from pathlib import Path

import pytest
from testfixtures import Replacer, TempDir
from typer.testing import CliRunner

from chimera.__main__ import app
from chimera.commands.agent import live_sessions

runner = CliRunner()


def _workspace(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = tmpdir.makedir('lycia')
    (ws / 'config.yaml').write_text('kind: workspace\n')
    project = ws / 'myproject'
    (project / 'worktrees' / 'g@agent').mkdir(parents=True)
    (project / 'config.yaml').write_text(f'kind: project\nrepo: {project}\n')
    monkeypatch.chdir(ws)  # at the workspace root: no project inferred from cwd
    return ws


def test_project_before_the_group(tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch) -> None:
    _workspace(tmpdir, monkeypatch)
    result = runner.invoke(app, ['-p', 'myproject', 'goal', 'ls'])
    assert result.exit_code == 0
    assert result.output.strip() == 'g'


def test_project_between_group_and_command(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmpdir, monkeypatch)
    result = runner.invoke(app, ['goal', '-p', 'myproject', 'ls'])
    assert result.exit_code == 0
    assert result.output.strip() == 'g'


def test_leaf_flag_wins_over_an_earlier_one(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmpdir, monkeypatch)
    result = runner.invoke(app, ['-p', 'nope', 'goal', 'ls', '-p', 'myproject'])
    assert result.exit_code == 0
    assert result.output.strip() == 'g'


def test_goal_and_actor_before_the_command(
    tmpdir: TempDir, monkeypatch: pytest.MonkeyPatch, replace: Replacer
) -> None:
    ws = _workspace(tmpdir, monkeypatch)
    (ws / 'myproject' / 'worktrees' / 'g@reviewer').mkdir()
    calls: list[object] = []
    replace.in_module(live_sessions, lambda worktree: [])
    replace.in_module(subprocess.run, lambda cmd, cwd=None, check=False: calls.append((cmd, cwd)))
    result = runner.invoke(app, ['agent', '-p', 'myproject', '-g', 'g', '-a', 'reviewer', 'start'])
    assert result.exit_code == 0
    worktree = (ws / 'myproject' / 'worktrees' / 'g@reviewer').resolve()
    assert calls == [(['claude', '--name', 'myproject@g@reviewer'], worktree)]
