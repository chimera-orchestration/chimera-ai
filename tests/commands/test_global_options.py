from hashlib import sha256
from pathlib import Path

from testfixtures import Replacer, TempDir, compare

from chimera.agent_env import ROLE_AGENT
from chimera.agents.claude import Claude
from chimera.prime import prime
from tests.cli import (
    Command,
    action_logs,
    capture_launches,
    context_sources,
    launched,
    launching,
    SESSION_ID,
)


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
    replace.on_class(Claude.live, lambda self, cwd=None: [])
    calls = capture_launches(replace)
    worktree = workspace_with_env / 'myproject' / 'worktrees' / 'g@reviewer'
    text = f'# Role: agent\n\n{prime(ROLE_AGENT, project="myproject", goal="g")}'
    digest = sha256(text.encode()).hexdigest()
    context = workspace_with_env / 'state' / 'context' / f'myproject@g@reviewer-{digest[:8]}.md'
    claude_cmd = [
        'claude',
        '--session-id',
        SESSION_ID,
        '--name',
        'myproject@g@reviewer',
        '--append-system-prompt-file',
        str(context),
    ]
    command.run('agent', '-p', 'myproject', '-g', 'g', '-a', 'reviewer', 'start').check(
        output=f'Launched agent in {worktree}',
        logging=[
            {
                'level': 'INFO',
                'command': 'agent start',
                'phase': 'start',
                'function': 'chimera.commands.agent.agent',
                'params': {
                    'prompt': None,
                    'goal': None,
                    'actor': None,
                    'project': None,
                    'dangerous': False,
                    'harness': None,
                    'model': None,
                    'dry': False,
                },
            },
            {
                'level': 'INFO',
                'session': 'myproject@g@reviewer',
                'path': str(context),
                'sha256': digest,
                'sources': context_sources(
                    workspace_with_env, 'agent', pinned=workspace_with_env / 'myproject'
                ),
                'message': 'context: rendered',
            },
            launching(claude_cmd, worktree),
            launched(claude_cmd, worktree),
            {'level': 'INFO', 'command': 'agent start', 'phase': 'end'},
        ],
    )
    compare(calls, expected=[(claude_cmd, worktree)])
