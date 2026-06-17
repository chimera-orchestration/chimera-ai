import os
from pathlib import Path

from giterator import Git
from giterator.testing import Repo
from testfixtures import Command, Replacer, TempDir, compare

from chimera.commands.agent import agent
from chimera.commands.goal import start as goal_start
from chimera.commands.goal.start import start


def _seeded_repo(tmpdir: TempDir) -> Repo:
    repo = Repo.make(tmpdir.path / 'repo')
    repo.commit_content('seed')
    return repo


def _project(tmpdir: TempDir, repo: Repo) -> Path:
    project = tmpdir.makedir('project')
    tmpdir.dump('project/config.yaml', {'kind': 'project', 'repo': str(repo.path)})
    os.chdir(project)  # the CLI infers the project (and its name) from cwd
    return project


def test_start_creates_worktrees_then_launches_the_agent(
    tmpdir: TempDir, replace: Replacer
) -> None:
    repo = _seeded_repo(tmpdir)
    worktrees = tmpdir.path / 'worktrees'
    calls: list[object] = []
    replace.in_module(
        agent,
        lambda worktree, name, prompt=None, extra=(): calls.append((worktree, name, prompt, extra)),
        module=goal_start,
    )
    created = start(repo.path, worktrees, 'g', 'proj@g@agent')
    compare(created, expected=worktrees / 'g@agent')
    tmpdir.compare(['g@agent'], path='worktrees', recursive=False)
    compare(Git(repo.path).branches(), expected=['g/agent', 'g/human', 'main'])
    # foreground (no prompt)
    compare(calls, expected=[(worktrees / 'g@agent', 'proj@g@agent', None, ())])


def test_start_passes_the_prompt_to_the_agent(tmpdir: TempDir, replace: Replacer) -> None:
    repo = _seeded_repo(tmpdir)
    worktrees = tmpdir.path / 'worktrees'
    calls: list[object] = []
    replace.in_module(
        agent,
        lambda worktree, name, prompt=None, extra=(): calls.append((worktree, name, prompt, extra)),
        module=goal_start,
    )
    start(repo.path, worktrees, 'g', 'proj@g@agent', prompt='do it')
    compare(calls, expected=[(worktrees / 'g@agent', 'proj@g@agent', 'do it', ())])


def test_goal_start_cli(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    repo = _seeded_repo(tmpdir)
    _project(tmpdir, repo)
    calls: list[object] = []  # stub the agent so real git runs but no claude launches
    replace.in_module(
        agent,
        lambda worktree, name, prompt=None, extra=(): calls.append((worktree, name, prompt, extra)),
        module=goal_start,
    )
    expected = Path.cwd() / 'worktrees' / 'feature-x@agent'
    command.run('goal', 'start', 'feature-x').check(
        output=f'Started feature-x in {expected}', logging=[('INFO', 'goal start')]
    )
    tmpdir.compare(['feature-x@agent'], path='project/worktrees', recursive=False)
    compare(calls, expected=[(expected, 'project@feature-x@agent', None, [])])


def test_goal_start_cli_with_prompt(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    repo = _seeded_repo(tmpdir)
    _project(tmpdir, repo)
    calls: list[object] = []
    replace.in_module(
        agent,
        lambda worktree, name, prompt=None, extra=(): calls.append((worktree, name, prompt, extra)),
        module=goal_start,
    )
    expected = Path.cwd() / 'worktrees' / 'feature-x@agent'
    command.run('goal', 'start', 'feature-x', 'go build it').check(
        output=f'Started feature-x in {expected}', logging=[('INFO', 'goal start')]
    )
    compare(calls, expected=[(expected, 'project@feature-x@agent', 'go build it', [])])


def test_goal_start_cli_passes_extra_flags_through(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    repo = _seeded_repo(tmpdir)
    _project(tmpdir, repo)
    calls: list[object] = []
    replace.in_module(
        agent,
        lambda worktree, name, prompt=None, extra=(): calls.append((worktree, name, prompt, extra)),
        module=goal_start,
    )
    expected = Path.cwd() / 'worktrees' / 'feature-x@agent'
    command.run('goal', 'start', 'feature-x', '--', '--model', 'opus').check(
        output=f'Started feature-x in {expected}', logging=[('INFO', 'goal start')]
    )
    compare(calls, expected=[(expected, 'project@feature-x@agent', None, ['--model', 'opus'])])
