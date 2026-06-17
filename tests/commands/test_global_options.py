import subprocess
from pathlib import Path

from testfixtures import Command, Replacer, TempDir, compare

from chimera.commands.agent import live_sessions


def _myproject(tmpdir: TempDir, workspace: Path) -> Path:
    project = workspace / 'myproject'
    (project / 'worktrees' / 'g@agent').mkdir(parents=True)
    tmpdir.dump(
        project / 'config.yaml',
        {'kind': 'project', 'repo': str(project)},
    )
    return project


def test_project_before_the_group(
    tmpdir: TempDir, workspace_with_env: Path, command: Command
) -> None:
    _myproject(tmpdir, workspace_with_env)
    command.run('-p', 'myproject', 'goal', 'ls').check(output='g', logging=[('INFO', 'goal ls')])


def test_project_between_group_and_command(
    tmpdir: TempDir, workspace_with_env: Path, command: Command
) -> None:
    _myproject(tmpdir, workspace_with_env)
    command.run('goal', '-p', 'myproject', 'ls').check(output='g', logging=[('INFO', 'goal ls')])


def test_leaf_flag_wins_over_an_earlier_one(
    tmpdir: TempDir, workspace_with_env: Path, command: Command
) -> None:
    _myproject(tmpdir, workspace_with_env)
    command.run('-p', 'nope', 'goal', 'ls', '-p', 'myproject').check(
        output='g', logging=[('INFO', 'goal ls')]
    )


def test_goal_and_actor_before_the_command(
    tmpdir: TempDir, workspace_with_env: Path, replace: Replacer, command: Command
) -> None:
    _myproject(tmpdir, workspace_with_env)
    (workspace_with_env / 'myproject' / 'worktrees' / 'g@reviewer').mkdir()
    calls: list[object] = []
    replace.in_module(live_sessions, lambda worktree: [])
    replace.in_module(subprocess.run, lambda cmd, cwd=None, check=False: calls.append((cmd, cwd)))
    worktree = workspace_with_env / 'myproject' / 'worktrees' / 'g@reviewer'
    command.run('agent', '-p', 'myproject', '-g', 'g', '-a', 'reviewer', 'start').check(
        output=f'Launched agent in {worktree}', logging=[('INFO', 'agent start')]
    )
    compare(calls, expected=[(['claude', '--name', 'myproject@g@reviewer'], worktree)])
