import subprocess
from pathlib import Path

from testfixtures import Command, Replacer, TempDir, compare

from chimera.commands.agent import live_sessions


def _workspace(tmpdir: TempDir, replace: Replacer) -> Path:
    ws = tmpdir.makedir('lycia')
    (ws / 'config.yaml').write_text('kind: workspace\n')
    project = ws / 'myproject'
    (project / 'worktrees' / 'g@agent').mkdir(parents=True)
    (project / 'config.yaml').write_text(f'kind: project\nrepo: {project}\n')
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    return ws


def test_project_before_the_group(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    _workspace(tmpdir, replace)
    command.run('-p', 'myproject', 'goal', 'ls').check(output='g', logging=[('INFO', 'goal ls')])


def test_project_between_group_and_command(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _workspace(tmpdir, replace)
    command.run('goal', '-p', 'myproject', 'ls').check(output='g', logging=[('INFO', 'goal ls')])


def test_leaf_flag_wins_over_an_earlier_one(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _workspace(tmpdir, replace)
    command.run('-p', 'nope', 'goal', 'ls', '-p', 'myproject').check(
        output='g', logging=[('INFO', 'goal ls')]
    )


def test_goal_and_actor_before_the_command(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _workspace(tmpdir, replace)
    (ws / 'myproject' / 'worktrees' / 'g@reviewer').mkdir()
    calls: list[object] = []
    replace.in_module(live_sessions, lambda worktree: [])
    replace.in_module(subprocess.run, lambda cmd, cwd=None, check=False: calls.append((cmd, cwd)))
    worktree = ws / 'myproject' / 'worktrees' / 'g@reviewer'
    command.run('agent', '-p', 'myproject', '-g', 'g', '-a', 'reviewer', 'start').check(
        output=f'Launched agent in {worktree}', logging=[('INFO', 'agent start')]
    )
    compare(calls, expected=[(['claude', '--name', 'myproject@g@reviewer'], worktree)])
