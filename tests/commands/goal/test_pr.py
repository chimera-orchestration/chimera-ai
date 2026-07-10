import subprocess
from collections.abc import Sequence
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from typing import Any

from giterator.testing import Repo
from testfixtures import LogCapture, Replacer, ShouldRaise, TempDir, compare
from testfixtures.loguru import LoguruSource

from chimera.commands.goal.pr import PrResult, pr
from chimera.commands.worktree.add import add
from chimera.config import UserError
from chimera.dry import Dry
from chimera.git import Git
from tests.cli import Command, action_logs

PR_URL = 'https://github.com/o/r/pull/7'


def _short(repo_path: Path, ref: str) -> str:
    return Git(repo_path).rev_parse(ref)


def _full(repo_path: Path, ref: str) -> str:
    return Git(repo_path).rev_parse(ref, short=False)


def _published(tmpdir: TempDir, git_repo: Repo) -> Path:
    """The goal 'g' stood up in a repo whose origin is a local bare clone; returns origin."""
    origin = tmpdir / 'origin'
    Git(tmpdir.path)('init', '--bare', '-b', 'main', str(origin))
    Git(git_repo.path)('remote', 'add', 'origin', str(origin))
    git_repo('push', '-q', 'origin', 'main')
    add(git_repo.path, tmpdir / 'worktrees', goal='g')
    return origin


_real_run = subprocess.run


def _outside(
    replace: Replacer,
    open_prs: str = '[]',
    title: str = 'Compressed why\n',
    claude_fails: bool = False,
    claude_missing: bool = False,
) -> list[tuple[list[str], str | None]]:
    """Stub the world outside git — gh and the title model — recording every call.

    Anything else (git runs through :data:`subprocess.run` too, via giterator) is
    delegated to the real thing.
    """
    calls: list[tuple[list[str], str | None]] = []

    def run(args: Sequence[str], *rest: Any, **kw: Any) -> CompletedProcess[Any]:
        cmd = list(args)
        if cmd[0] not in ('gh', 'claude'):
            return _real_run(args, *rest, **kw)
        fed = kw.get('input')
        calls.append((cmd, fed if isinstance(fed, str) else None))
        if cmd[0] == 'claude':
            if claude_missing:
                raise FileNotFoundError('claude')
            if claude_fails:
                raise CalledProcessError(1, cmd, stderr='model overloaded')
            return CompletedProcess(cmd, 0, stdout=title, stderr='')
        if cmd[:3] == ['gh', 'pr', 'list']:
            return CompletedProcess(cmd, 0, stdout=open_prs, stderr='')
        return CompletedProcess(cmd, 0, stdout=f'{PR_URL}\n', stderr='')

    replace.in_module(subprocess.run, run)
    return calls


def _created_with(calls: list[tuple[list[str], str | None]]) -> list[str] | None:
    return next((args for args, _ in calls if args[:3] == ['gh', 'pr', 'create']), None)


def _prompt_fed(calls: list[tuple[list[str], str | None]]) -> str:
    return next(args[2] for args, _ in calls if args[0] == 'claude')


def _pr(tmpdir: TempDir, repo_path: Path, goal: str = 'g', **kw: Any) -> PrResult:
    return pr(repo_path, 'proj', tmpdir / 'prompts', goal, **kw)


def test_single_commit_titles_and_bodies_from_its_message(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    origin = _published(tmpdir, git_repo)
    Repo(tmpdir / 'worktrees' / 'g@agent').commit_content(
        'work', message='Do the thing\n\nBecause reasons.'
    )
    calls = _outside(replace)
    result = _pr(tmpdir, git_repo.path)
    compare(
        result,
        expected=PrResult(
            'g/agent',
            'g',
            'main',
            _short(git_repo.path, 'g/agent'),
            'Do the thing',
            'Because reasons.',
            PR_URL,
            True,
        ),
    )
    compare(_full(origin, 'g'), expected=_full(git_repo.path, 'g/agent'))
    assert not any(args[0] == 'claude' for args, _ in calls)  # nothing to compress
    created = _created_with(calls)
    assert created is not None
    assert '--title' in created and created[created.index('--title') + 1] == 'Do the thing'
    assert created[created.index('--body') + 1] == 'Because reasons.'
    assert '--draft' not in created


def test_multi_commit_description_is_written_by_the_model(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    _published(tmpdir, git_repo)
    agent = Repo(tmpdir / 'worktrees' / 'g@agent')
    agent.commit_content('one', message='First why\n\nDetail one.')
    agent.commit_content('two', message='Second why')
    calls = _outside(replace, title='Compressed why\n\nBecause of BCK-1234.\n')
    result = _pr(tmpdir, git_repo.path)
    compare(result.title, expected='Compressed why')
    compare(result.body, expected='Because of BCK-1234.')
    fed = _prompt_fed(calls)  # the default template, with the commit messages substituted in
    assert 'First why' in fed and 'Detail one.' in fed and 'Second why' in fed
    assert 'succinct summary of WHY' in fed
    assert '`g` of proj, targeting `main`' in fed
    created = _created_with(calls)
    assert created is not None
    compare(created[created.index('--title') + 1], expected='Compressed why')
    compare(created[created.index('--body') + 1], expected='Because of BCK-1234.')


def test_projects_own_pr_template_wins(tmpdir: TempDir, git_repo: Repo, replace: Replacer) -> None:
    _published(tmpdir, git_repo)
    agent = Repo(tmpdir / 'worktrees' / 'g@agent')
    agent.commit_content('one', message='First why')
    agent.commit_content('two', message='Second why')
    tmpdir.write('prompts/pr.md', 'THE $PROJECT DANCE for $GOAL:\n\n$COMMITS\n')
    calls = _outside(replace)
    _pr(tmpdir, git_repo.path)
    compare(_prompt_fed(calls), expected='THE proj DANCE for g:\n\nFirst why\n\nSecond why\n')


def test_refuses_when_the_model_fails(tmpdir: TempDir, git_repo: Repo, replace: Replacer) -> None:
    _published(tmpdir, git_repo)
    agent = Repo(tmpdir / 'worktrees' / 'g@agent')
    agent.commit_content('one')
    agent.commit_content('two')
    _outside(replace, claude_fails=True)
    with ShouldRaise(
        UserError(
            'could not write the PR description (model overloaded) — '
            'write it yourself: gh pr create --head g --base main'
        )
    ):
        _pr(tmpdir, git_repo.path)


def test_refuses_when_the_model_binary_is_missing(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    _published(tmpdir, git_repo)
    agent = Repo(tmpdir / 'worktrees' / 'g@agent')
    agent.commit_content('one')
    agent.commit_content('two')
    _outside(replace, claude_missing=True)
    with ShouldRaise(
        UserError(
            'could not write the PR description (claude) — '
            'write it yourself: gh pr create --head g --base main'
        )
    ):
        _pr(tmpdir, git_repo.path)


def test_refuses_when_the_model_answers_nothing(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    _published(tmpdir, git_repo)
    agent = Repo(tmpdir / 'worktrees' / 'g@agent')
    agent.commit_content('one')
    agent.commit_content('two')
    _outside(replace, title='  \n')
    with ShouldRaise(
        UserError(
            'the PR description model answered nothing — '
            'write it yourself: gh pr create --head g --base main'
        )
    ):
        _pr(tmpdir, git_repo.path)


def test_existing_pr_is_reported_not_duplicated(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    origin = _published(tmpdir, git_repo)
    Repo(tmpdir / 'worktrees' / 'g@agent').commit_content('work')
    calls = _outside(replace, open_prs='[{"url": "https://github.com/o/r/pull/3"}]')
    result = _pr(tmpdir, git_repo.path)
    compare(result.url, expected='https://github.com/o/r/pull/3')
    assert not result.created
    assert _created_with(calls) is None
    compare(_full(origin, 'g'), expected=_full(git_repo.path, 'g/agent'))  # push still updates


def test_dry_resolves_everything_but_pushes_and_opens_nothing(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    origin = _published(tmpdir, git_repo)
    Repo(tmpdir / 'worktrees' / 'g@agent').commit_content(
        'work', message='Do the thing\n\nBecause reasons.'
    )
    calls = _outside(replace)
    result = _pr(tmpdir, git_repo.path, dry=Dry(True))
    compare(
        result,
        expected=PrResult(
            'g/agent',
            'g',
            'main',
            _short(git_repo.path, 'g/agent'),
            'Do the thing',
            'Because reasons.',
            None,
            False,
        ),
    )
    assert not Git(origin).ref_exists('g')
    assert _created_with(calls) is None


def test_draft_rides_the_create(tmpdir: TempDir, git_repo: Repo, replace: Replacer) -> None:
    _published(tmpdir, git_repo)
    Repo(tmpdir / 'worktrees' / 'g@agent').commit_content('work')
    calls = _outside(replace)
    _pr(tmpdir, git_repo.path, draft=True)
    created = _created_with(calls)
    assert created is not None and '--draft' in created


def test_into_proposes_against_a_local_only_base(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    _published(tmpdir, git_repo)
    Git(git_repo.path)('branch', 'release', 'main')  # exists locally, never pushed
    Repo(tmpdir / 'worktrees' / 'g@agent').commit_content('work')
    calls = _outside(replace)
    result = _pr(tmpdir, git_repo.path, into='release')
    compare(result.base, expected='release')
    created = _created_with(calls)
    assert created is not None and created[created.index('--base') + 1] == 'release'


def test_refuses_without_an_origin(tmpdir: TempDir, git_repo: Repo) -> None:
    add(git_repo.path, tmpdir / 'worktrees', goal='g')
    with ShouldRaise(
        UserError('no origin to push g to — publish the project first: ch project push <url>')
    ):
        _pr(tmpdir, git_repo.path)


def test_refuses_a_goal_with_no_branches(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    _published(tmpdir, git_repo)
    _outside(replace)
    with ShouldRaise(UserError("nothing to propose — no actor branches for goal 'ghost'")):
        _pr(tmpdir, git_repo.path, 'ghost')


def test_refuses_when_there_is_nothing_beyond_the_base(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    _published(tmpdir, git_repo)
    _outside(replace)
    with ShouldRaise(UserError('g/agent has no commits beyond origin/main — nothing to propose')):
        _pr(tmpdir, git_repo.path, fetch=False)


def test_refuses_a_goal_branch_as_base(tmpdir: TempDir, git_repo: Repo, replace: Replacer) -> None:
    _published(tmpdir, git_repo)
    _outside(replace)
    with ShouldRaise(UserError("g/agent is one of g's own branches — name a base like main")):
        _pr(tmpdir, git_repo.path, into='g/agent')


def test_refuses_a_missing_base(tmpdir: TempDir, git_repo: Repo, replace: Replacer) -> None:
    _published(tmpdir, git_repo)
    _outside(replace)
    with ShouldRaise(UserError('no branch release to propose against')):
        _pr(tmpdir, git_repo.path, into='release')


def test_refuses_diverged_actors_without_advertising_force(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    _published(tmpdir, git_repo)
    Repo(tmpdir / 'worktrees' / 'g@agent').commit_content('agent-work')
    git = Git(git_repo.path)
    git('branch', '--no-track', 'g/human', 'main')
    checkout = tmpdir / 'human'
    git('worktree', 'add', str(checkout), 'g/human')
    Repo(checkout).commit_content('human-work')
    _outside(replace)
    with ShouldRaise(
        UserError(
            'no actor branch contains all the others (g/agent, g/human) — '
            'ch goal sync g so one does'
        )
    ):
        _pr(tmpdir, git_repo.path)


def test_gh_list_failure_surfaces(tmpdir: TempDir, git_repo: Repo, replace: Replacer) -> None:
    _published(tmpdir, git_repo)
    Repo(tmpdir / 'worktrees' / 'g@agent').commit_content('work')

    def run(args: Sequence[str], *rest: Any, **kw: Any) -> CompletedProcess[Any]:
        cmd = list(args)
        if cmd[0] != 'gh':
            return _real_run(args, *rest, **kw)
        return CompletedProcess(cmd, 1, stdout='', stderr='boom')

    replace.in_module(subprocess.run, run)
    with ShouldRaise(UserError('gh pr list failed: boom')):
        _pr(tmpdir, git_repo.path)


def test_gh_create_failure_surfaces(tmpdir: TempDir, git_repo: Repo, replace: Replacer) -> None:
    _published(tmpdir, git_repo)
    Repo(tmpdir / 'worktrees' / 'g@agent').commit_content('work')

    def run(args: Sequence[str], *rest: Any, **kw: Any) -> CompletedProcess[Any]:
        cmd = list(args)
        if cmd[0] != 'gh':
            return _real_run(args, *rest, **kw)
        if cmd[:3] == ['gh', 'pr', 'list']:
            return CompletedProcess(cmd, 0, stdout='[]', stderr='')
        return CompletedProcess(cmd, 1, stdout='', stderr='boom')

    replace.in_module(subprocess.run, run)
    with ShouldRaise(UserError('gh pr create failed: boom')):
        _pr(tmpdir, git_repo.path)


def test_logs_the_push_and_the_pr(tmpdir: TempDir, git_repo: Repo, replace: Replacer) -> None:
    _published(tmpdir, git_repo)
    Repo(tmpdir / 'worktrees' / 'g@agent').commit_content('work', message='Do the thing')
    _outside(replace)
    tip = _full(git_repo.path, 'g/agent')
    with LogCapture(LoguruSource(('message', 'extra'), level='INFO')) as log:
        _pr(tmpdir, git_repo.path)
    log.check(
        ('goal pr: source', {'source': 'g/agent', 'candidates': ['g/agent']}),
        (
            'goal pr: refs',
            {
                'goal': 'g',
                'source': 'g/agent',
                'git': {'before': {}, 'after': {'origin/g': tip}},
            },
        ),
        ('goal pr: opened', {'url': PR_URL, 'title': 'Do the thing', 'goal': 'g'}),
    )


def test_goal_pr_cli(tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command) -> None:
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    _published(tmpdir, git_repo)
    Repo(tmpdir / 'worktrees' / 'g@agent').commit_content('work', message='Do the thing')
    _outside(replace)
    tip_short, tip = _short(git_repo.path, 'g/agent'), _full(git_repo.path, 'g/agent')
    start, end = action_logs(
        'goal pr',
        'chimera.commands.goal.pr.pr',
        {
            'goal': 'g',
            'into': None,
            'draft': False,
            'offline': False,
            'dry': False,
            'project': None,
        },
    )
    command.run('goal', 'pr', 'g').check(
        output=f'Pushed g/agent to origin as g ({tip_short})\nOpened PR: {PR_URL}',
        logging=[
            start,
            {
                'level': 'INFO',
                'source': 'g/agent',
                'candidates': ['g/agent'],
                'message': 'goal pr: source',
            },
            {
                'level': 'INFO',
                'goal': 'g',
                'source': 'g/agent',
                'git': {'before': {}, 'after': {'origin/g': tip}},
                'message': 'goal pr: refs',
            },
            {
                'level': 'INFO',
                'url': PR_URL,
                'title': 'Do the thing',
                'goal': 'g',
                'message': 'goal pr: opened',
            },
            end,
        ],
    )


def test_goal_pr_cli_reports_an_open_pr(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    _published(tmpdir, git_repo)
    Repo(tmpdir / 'worktrees' / 'g@agent').commit_content('work')
    _outside(replace, open_prs='[{"url": "https://github.com/o/r/pull/3"}]')
    tip_short = _short(git_repo.path, 'g/agent')
    command.run('goal', 'pr', 'g').check(
        output=(
            f'Pushed g/agent to origin as g ({tip_short})\n'
            f'PR already open: https://github.com/o/r/pull/3'
        ),
        logging=[
            action_logs(
                'goal pr',
                'chimera.commands.goal.pr.pr',
                {
                    'goal': 'g',
                    'into': None,
                    'draft': False,
                    'offline': False,
                    'dry': False,
                    'project': None,
                },
            )[0],
            {
                'level': 'INFO',
                'source': 'g/agent',
                'candidates': ['g/agent'],
                'message': 'goal pr: source',
            },
            {
                'level': 'INFO',
                'goal': 'g',
                'source': 'g/agent',
                'git': {
                    'before': {},
                    'after': {'origin/g': _full(git_repo.path, 'g/agent')},
                },
                'message': 'goal pr: refs',
            },
            {
                'level': 'INFO',
                'url': 'https://github.com/o/r/pull/3',
                'goal': 'g',
                'message': 'goal pr: existing',
            },
            action_logs(
                'goal pr',
                'chimera.commands.goal.pr.pr',
                {
                    'goal': 'g',
                    'into': None,
                    'draft': False,
                    'offline': False,
                    'dry': False,
                    'project': None,
                },
            )[1],
        ],
    )


def test_goal_pr_cli_dry_previews_title_and_body(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    _published(tmpdir, git_repo)
    Repo(tmpdir / 'worktrees' / 'g@agent').commit_content(
        'work', message='Do the thing\n\nBecause reasons.'
    )
    _outside(replace)
    tip_short = _short(git_repo.path, 'g/agent')
    start, end = action_logs(
        'goal pr',
        'chimera.commands.goal.pr.pr',
        {'goal': 'g', 'into': None, 'draft': False, 'offline': False, 'dry': True, 'project': None},
    )
    command.run('goal', 'pr', 'g', '--dry').check(
        output=(
            f'Would push g/agent to origin as g ({tip_short})\n'
            f'Would open a PR against main:\n'
            f'title: Do the thing\n'
            f'Because reasons.'
        ),
        logging=[
            start,
            {
                'level': 'INFO',
                'source': 'g/agent',
                'candidates': ['g/agent'],
                'message': 'goal pr: source',
            },
            end,
        ],
    )
