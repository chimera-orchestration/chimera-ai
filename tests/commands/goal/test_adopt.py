import os
from collections.abc import Sequence
from pathlib import Path

from giterator import Git
from giterator.testing import Repo
from testfixtures import LogCapture, Replacer, ShouldRaise, TempDir, compare
from testfixtures.loguru import LoguruSource

from chimera.commands.agent import agent
from chimera.commands.goal import adopt as goal_adopt
from chimera.commands.goal.adopt import adopt
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

    replace.in_module(agent, record, module=goal_adopt)
    return calls


def _rev(repo_path: Path, ref: str) -> str:
    return Git(repo_path).rev_parse(ref, short=False)


def test_adopt_restructures_the_branch_then_launches_the_agent(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    git_repo('checkout', '-b', 'feature')
    tip = git_repo.commit_content('feature-work', short=False)
    git_repo('checkout', 'main')
    worktrees = tmpdir / 'worktrees'
    calls = _stub_agent(replace)
    compare(
        adopt(git_repo.path, worktrees, 'feature', 'proj@feature@agent'),
        expected=worktrees / 'feature@agent',
    )
    tmpdir.compare(['feature@agent'], path='worktrees', recursive=False)
    # the original branch is gone, replaced by the two actor branches off its tip
    compare(Git(git_repo.path).branches(), expected=['feature/agent', 'feature/human', 'main'])
    compare(_rev(git_repo.path, 'feature/human'), expected=tip)
    compare(_rev(git_repo.path, 'feature/agent'), expected=tip)
    compare(calls, expected=[(worktrees / 'feature@agent', 'proj@feature@agent', None, (), False)])


def test_adopt_keeps_the_adopted_branchs_upstream(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    git_repo('checkout', '-b', 'feature')
    git_repo.commit_content('feature-work')
    git_repo('checkout', 'main')
    git_repo('init', '--bare', str(tmpdir / 'origin'))
    git_repo('remote', 'add', 'origin', str(tmpdir / 'origin'))
    git_repo('push', '-u', 'origin', 'feature')
    _stub_agent(replace)
    adopt(git_repo.path, tmpdir / 'worktrees', 'feature', 'proj@feature@agent')
    # the rename carries the branch's config section, so the human branch keeps tracking
    compare(
        Git(git_repo.path)('rev-parse', '--abbrev-ref', 'feature/human@{upstream}').strip(),
        expected='origin/feature',
    )


def test_adopt_agent_worktree_checks_out_the_adopted_work(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    git_repo('checkout', '-b', 'feature')
    tip = git_repo.commit_content('feature-work', short=False)
    git_repo('checkout', 'main')
    worktrees = tmpdir / 'worktrees'
    _stub_agent(replace)
    adopt(git_repo.path, worktrees, 'feature', 'proj@feature@agent')
    compare(_rev(worktrees / 'feature@agent', 'HEAD'), expected=tip)
    compare(
        Git(worktrees / 'feature@agent')('rev-parse', '--abbrev-ref', 'HEAD').strip(),
        expected='feature/agent',
    )


def test_adopt_passes_the_prompt_to_the_agent(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    git_repo('checkout', '-b', 'feature')
    git_repo.commit_content('feature-work')
    git_repo('checkout', 'main')
    worktrees = tmpdir / 'worktrees'
    calls = _stub_agent(replace)
    adopt(git_repo.path, worktrees, 'feature', 'proj@feature@agent', prompt='do it')
    compare(
        calls, expected=[(worktrees / 'feature@agent', 'proj@feature@agent', 'do it', (), False)]
    )


def test_adopt_is_idempotent(tmpdir: TempDir, git_repo: Repo, replace: Replacer) -> None:
    git_repo('checkout', '-b', 'feature')
    git_repo.commit_content('feature-work')
    git_repo('checkout', 'main')
    worktrees = tmpdir / 'worktrees'
    _stub_agent(replace)
    adopt(git_repo.path, worktrees, 'feature', 'proj@feature@agent')
    branches = Git(git_repo.path).branches()
    # re-running neither re-restructures the branches nor re-creates the worktree
    compare(
        adopt(git_repo.path, worktrees, 'feature', 'proj@feature@agent'),
        expected=worktrees / 'feature@agent',
    )
    compare(Git(git_repo.path).branches(), expected=branches)
    tmpdir.compare(['feature@agent'], path='worktrees', recursive=False)


def test_adopt_completes_a_half_restructured_goal(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    git_repo('branch', '-m', 'main', 'feature/human')  # only the human branch exists
    git_repo('checkout', '-b', 'main')
    tip = _rev(git_repo.path, 'feature/human')
    worktrees = tmpdir / 'worktrees'
    _stub_agent(replace)
    adopt(git_repo.path, worktrees, 'feature', 'proj@feature@agent')
    compare(_rev(git_repo.path, 'feature/agent'), expected=tip)
    tmpdir.compare(['feature@agent'], path='worktrees', recursive=False)


def test_adopt_refuses_when_no_branch_to_adopt(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    _stub_agent(replace)
    with ShouldRaise(RuntimeError("no branch 'ghost' to adopt")):
        adopt(git_repo.path, tmpdir / 'worktrees', 'ghost', 'proj@ghost@agent')


def test_adopt_logs_the_refs_before_and_after(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    git_repo('checkout', '-b', 'feature')
    tip = git_repo.commit_content('feature-work', short=False)
    git_repo('checkout', 'main')
    worktrees = tmpdir / 'worktrees'
    _stub_agent(replace)
    with LogCapture(LoguruSource(('message', 'extra'), level='INFO')) as log:
        adopt(git_repo.path, worktrees, 'feature', 'proj@feature@agent')
    log.check(
        (
            'goal adopt: refs',
            {
                'goal': 'feature',
                # before: the bare branch; after: the actor branches that replaced it
                'git': {
                    'before': {'feature': tip},
                    'after': {'feature/human': tip, 'feature/agent': tip},
                },
                'worktree': str(worktrees / 'feature@agent'),
            },
        ),
    )


def test_adopt_restructures_a_branch_checked_out_elsewhere(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    git_repo('branch', 'feature')
    git_repo('worktree', 'add', str(tmpdir / 'elsewhere'), 'feature')  # feature is checked out
    worktrees = tmpdir / 'worktrees'
    _stub_agent(replace)
    adopt(git_repo.path, worktrees, 'feature', 'proj@feature@agent')
    compare(Git(git_repo.path).branches(), expected=['feature/agent', 'feature/human', 'main'])


def _adopt_logs(base: str, worktree: object, *, dangerous: bool = False) -> list[dict[str, object]]:
    """start / `goal adopt: refs` event / end for adopting the `feature-x` branch."""
    start, end = action_logs(
        'goal adopt',
        'chimera.commands.goal.adopt.adopt',
        {'goal': 'feature-x', 'prompt': None, 'project': None, 'dangerous': dangerous},
    )
    event = {
        'level': 'INFO',
        'goal': 'feature-x',
        'git': {
            'before': {'feature-x': base},
            'after': {'feature-x/agent': base, 'feature-x/human': base},
        },
        'worktree': str(worktree),
        'message': 'goal adopt: refs',
    }
    return [start, event, end]


def test_goal_adopt_cli(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, git_repo)
    git_repo('branch', 'feature-x')
    calls = _stub_agent(replace)
    base = Git(git_repo.path)('rev-parse', 'feature-x').strip()
    expected = Path.cwd() / 'worktrees' / 'feature-x@agent'
    command.run('goal', 'adopt', 'feature-x').check(
        output=f'Adopted feature-x in {expected}',
        logging=_adopt_logs(base, expected),
    )
    tmpdir.compare(['feature-x@agent'], path='project/worktrees', recursive=False)
    compare(Git(git_repo.path).branches(), expected=['feature-x/agent', 'feature-x/human', 'main'])
    compare(calls, expected=[(expected, 'project@feature-x@agent', None, [], False)])


def test_goal_adopt_cli_passes_extra_flags_through(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, git_repo)
    git_repo('branch', 'feature-x')
    calls = _stub_agent(replace)
    base = Git(git_repo.path)('rev-parse', 'feature-x').strip()
    expected = Path.cwd() / 'worktrees' / 'feature-x@agent'
    command.run('goal', 'adopt', 'feature-x', '--', '--model', 'opus').check(
        output=f'Adopted feature-x in {expected}',
        logging=_adopt_logs(base, expected),
    )
    compare(
        calls, expected=[(expected, 'project@feature-x@agent', None, ['--model', 'opus'], False)]
    )


def test_goal_adopt_cli_dangerous(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, git_repo)
    git_repo('branch', 'feature-x')
    calls = _stub_agent(replace)
    base = Git(git_repo.path)('rev-parse', 'feature-x').strip()
    expected = Path.cwd() / 'worktrees' / 'feature-x@agent'
    command.run('goal', 'adopt', 'feature-x', '--dangerous').check(
        output=f'Adopted feature-x in {expected}',
        logging=_adopt_logs(base, expected, dangerous=True),
    )
    compare(calls, expected=[(expected, 'project@feature-x@agent', None, [], True)])
