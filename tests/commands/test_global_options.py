import subprocess
from pathlib import Path

from testfixtures import Replacer, TempDir, compare

from chimera.agents.claude import live_sessions
from tests.cli import Command, action_logs


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
    command.run('-p', 'myproject', 'goal', 'ls').check(
        output='g',
        logging=action_logs(
            'goal ls', 'chimera.commands.goal.ls.goals_in_scope', {'project': None}
        ),
    )


def test_project_between_group_and_command(
    tmpdir: TempDir, workspace_with_env: Path, command: Command
) -> None:
    _myproject(tmpdir, workspace_with_env)
    command.run('goal', '-p', 'myproject', 'ls').check(
        output='g',
        logging=action_logs(
            'goal ls', 'chimera.commands.goal.ls.goals_in_scope', {'project': None}
        ),
    )


def test_leaf_flag_wins_over_an_earlier_one(
    tmpdir: TempDir, workspace_with_env: Path, command: Command
) -> None:
    _myproject(tmpdir, workspace_with_env)
    command.run('-p', 'nope', 'goal', 'ls', '-p', 'myproject').check(
        output='g',
        logging=action_logs(
            'goal ls', 'chimera.commands.goal.ls.goals_in_scope', {'project': 'myproject'}
        ),
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
        output=f'Launched agent in {worktree}',
        logging=action_logs(
            'agent start',
            'chimera.commands.agent.agent',
            {
                'prompt': None,
                'goal': None,
                'actor': None,
                'project': None,
                'dangerous': False,
                'harness': None,
                'model': None,
                'dry': False,
            },
        ),
    )
    claude_cmd = ['claude', '--name', 'myproject@g@reviewer']
    compare(calls, expected=[(claude_cmd, worktree)])
