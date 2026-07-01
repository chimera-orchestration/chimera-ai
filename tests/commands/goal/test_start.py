import os
from collections.abc import Sequence
from pathlib import Path

from giterator import Git
from giterator.testing import Repo
from testfixtures import Replacer, TempDir, compare

from chimera.commands.agent import agent
from chimera.commands.goal import start as goal_start
from chimera.commands.goal.start import start
from tests.cli import Command, action_logs


def _project(tmpdir: TempDir, repo: Repo) -> Path:
    project = tmpdir.makedir('project')
    tmpdir.dump('project/config.yaml', {'kind': 'project', 'repo': str(repo.path)})
    os.chdir(project)  # the CLI infers the project (and its name) from cwd
    return project


def _stub_agent(replace: Replacer) -> list[object]:
    calls: list[object] = []

    def record(
        worktree: Path,
        name: str,
        prompt: str | None = None,
        extra: Sequence[str] = (),
        dangerous: bool = False,
    ) -> None:
        calls.append((worktree, name, prompt, extra, dangerous))

    replace.in_module(agent, record, module=goal_start)
    return calls


def test_start_creates_worktrees_then_launches_the_agent(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    worktrees = tmpdir / 'worktrees'
    calls = _stub_agent(replace)
    compare(start(git_repo.path, worktrees, 'g', 'proj@g@agent'), expected=worktrees / 'g@agent')
    tmpdir.compare(['g@agent'], path='worktrees', recursive=False)
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'g/human', 'main'])
    # foreground (no prompt), not dangerous
    compare(calls, expected=[(worktrees / 'g@agent', 'proj@g@agent', None, (), False)])


def test_start_passes_the_prompt_to_the_agent(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    worktrees = tmpdir / 'worktrees'
    calls = _stub_agent(replace)
    start(git_repo.path, worktrees, 'g', 'proj@g@agent', prompt='do it')
    compare(calls, expected=[(worktrees / 'g@agent', 'proj@g@agent', 'do it', (), False)])


def test_start_passes_dangerous_to_the_agent(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    worktrees = tmpdir / 'worktrees'
    calls = _stub_agent(replace)
    start(git_repo.path, worktrees, 'g', 'proj@g@agent', dangerous=True)
    compare(calls, expected=[(worktrees / 'g@agent', 'proj@g@agent', None, (), True)])


def test_goal_start_cli(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, git_repo)
    calls = _stub_agent(replace)  # stub the agent so real git runs but no claude launches
    expected = Path.cwd() / 'worktrees' / 'feature-x@agent'
    command.run('goal', 'start', 'feature-x').check(
        output=f'Started feature-x in {expected}',
        logging=action_logs(
            'goal start',
            'chimera.commands.goal.start.start',
            {
                'goal': 'feature-x',
                'prompt': None,
                'frm': None,
                'project': None,
                'dangerous': False,
                'offline': False,
            },
        ),
    )
    tmpdir.compare(['feature-x@agent'], path='project/worktrees', recursive=False)
    compare(calls, expected=[(expected, 'project@feature-x@agent', None, [], False)])


def test_goal_start_cli_with_prompt(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, git_repo)
    calls = _stub_agent(replace)
    expected = Path.cwd() / 'worktrees' / 'feature-x@agent'
    command.run('goal', 'start', 'feature-x', 'go build it').check(
        output=f'Started feature-x in {expected}',
        logging=action_logs(
            'goal start',
            'chimera.commands.goal.start.start',
            {
                'goal': 'feature-x',
                'prompt': 'go build it',
                'frm': None,
                'project': None,
                'dangerous': False,
                'offline': False,
            },
        ),
    )
    compare(calls, expected=[(expected, 'project@feature-x@agent', 'go build it', [], False)])


def test_goal_start_cli_dangerous(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, git_repo)
    calls = _stub_agent(replace)
    expected = Path.cwd() / 'worktrees' / 'feature-x@agent'
    command.run('goal', 'start', 'feature-x', '--dangerous').check(
        output=f'Started feature-x in {expected}',
        logging=action_logs(
            'goal start',
            'chimera.commands.goal.start.start',
            {
                'goal': 'feature-x',
                'prompt': None,
                'frm': None,
                'project': None,
                'dangerous': True,
                'offline': False,
            },
        ),
    )
    compare(calls, expected=[(expected, 'project@feature-x@agent', None, [], True)])


def test_goal_start_cli_offline(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, git_repo)
    calls = _stub_agent(replace)
    expected = Path.cwd() / 'worktrees' / 'feature-x@agent'
    command.run('goal', 'start', 'feature-x', '--offline').check(
        output=f'Started feature-x in {expected}',
        logging=action_logs(
            'goal start',
            'chimera.commands.goal.start.start',
            {
                'goal': 'feature-x',
                'prompt': None,
                'frm': None,
                'project': None,
                'dangerous': False,
                'offline': True,
            },
        ),
    )
    compare(calls, expected=[(expected, 'project@feature-x@agent', None, [], False)])


def test_goal_start_cli_passes_extra_flags_through(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, git_repo)
    calls = _stub_agent(replace)
    expected = Path.cwd() / 'worktrees' / 'feature-x@agent'
    command.run('goal', 'start', 'feature-x', '--', '--model', 'opus').check(
        output=f'Started feature-x in {expected}',
        logging=action_logs(
            'goal start',
            'chimera.commands.goal.start.start',
            {
                'goal': 'feature-x',
                'prompt': None,
                'frm': None,
                'project': None,
                'dangerous': False,
                'offline': False,
            },
        ),
    )
    compare(
        calls, expected=[(expected, 'project@feature-x@agent', None, ['--model', 'opus'], False)]
    )
