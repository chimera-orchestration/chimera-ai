import os
from pathlib import Path

from giterator import Git
from giterator.testing import Repo
from testfixtures import LogCapture, Replacer, ShouldRaise, TempDir, compare
from testfixtures.loguru import LoguruSource

from chimera.commands.project.push import push
from chimera.config import UserError
from chimera.dry import Dry
from tests.cli import Command, action_logs


def _target(tmpdir: TempDir) -> Path:
    target = tmpdir.makedir('remote.git')
    Git(target)('init', '--bare', '-b', 'main')
    return target


def _cli_project(tmpdir: TempDir, repo: Repo, replace: Replacer) -> None:
    workspace = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    tmpdir.dump('lycia/myproj/config.yaml', {'kind': 'project', 'repo': str(repo.path)})
    replace.in_environ('CHIMERA_WORKSPACE', str(workspace))
    os.chdir(workspace / 'myproj')  # the CLI infers the project from cwd


def test_push_publishes_the_default_branch_and_wires_origin(
    tmpdir: TempDir, git_repo: Repo
) -> None:
    target = _target(tmpdir)
    compare(push(git_repo.path, str(target)), expected='main')
    git = Git(git_repo.path)
    sha = git.rev_parse('main', short=False)
    compare(Git(target).rev_parse('main', short=False), expected=sha)
    compare(git('remote', 'get-url', 'origin').strip(), expected=str(target))
    compare(git.rev_parse('origin/main', short=False), expected=sha)
    compare(
        git('symbolic-ref', 'refs/remotes/origin/HEAD').strip(),
        expected='refs/remotes/origin/main',
    )
    compare(
        git('for-each-ref', '--format=%(upstream:short)', 'refs/heads/main').strip(),
        expected='origin/main',
    )


def test_push_publishes_only_the_default_branch(tmpdir: TempDir, git_repo: Repo) -> None:
    target = _target(tmpdir)
    git_repo('branch', 'goal/agent')
    push(git_repo.path, str(target))
    compare(Git(target).branches(), expected=['main'])


def test_push_sets_head_on_a_remote_whose_unborn_head_differs(
    tmpdir: TempDir, git_repo: Repo
) -> None:
    target = tmpdir.makedir('remote.git')
    Git(target)('init', '--bare', '-b', 'master')  # unborn HEAD names a branch we won't push
    push(git_repo.path, str(target))
    compare(
        Git(git_repo.path)('symbolic-ref', 'refs/remotes/origin/HEAD').strip(),
        expected='refs/remotes/origin/main',
    )


def test_push_refuses_when_an_origin_already_exists(tmpdir: TempDir, git_repo: Repo) -> None:
    url = str(_target(tmpdir))
    git_repo('remote', 'add', 'origin', url)
    with ShouldRaise(
        UserError(f'{git_repo.path} already has an origin ({url}) — change it with git remote')
    ):
        push(git_repo.path, url)


def test_push_refuses_a_repo_with_no_commits(tmpdir: TempDir) -> None:
    repo = Repo.make(tmpdir / 'empty')
    with ShouldRaise(UserError(f'{repo.path} has no main branch to push — commit something first')):
        push(repo.path, str(tmpdir / 'remote.git'))


def test_push_failure_records_no_origin(tmpdir: TempDir, git_repo: Repo) -> None:
    with ShouldRaise(UserError, match='failed — no origin recorded'):
        push(git_repo.path, str(tmpdir / 'nowhere'))
    assert not Git(git_repo.path)('remote').strip()  # no config written


def test_push_dry_mutates_nothing(tmpdir: TempDir, git_repo: Repo) -> None:
    target = _target(tmpdir)
    with LogCapture(LoguruSource(('message', 'extra'), level='INFO')) as log:
        compare(push(git_repo.path, str(target), dry=Dry(on=True)), expected='main')
    log.check()  # no pushed line either
    compare(Git(target).branches(), expected=[])
    git = Git(git_repo.path)
    assert not git('remote').strip()
    assert not git('for-each-ref', '--format=%(upstream:short)', 'refs/heads/main').strip()


def test_push_logs_the_pushed_branch(tmpdir: TempDir, git_repo: Repo) -> None:
    target = _target(tmpdir)
    with LogCapture(LoguruSource(('message', 'extra'), level='INFO')) as log:
        push(git_repo.path, str(target))
    log.check(
        (
            'project push: pushed',
            {
                'url': str(target),
                'branch': 'main',
                'sha': Git(git_repo.path).rev_parse('main', short=False),
            },
        ),
    )


def test_project_push_cli(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    _cli_project(tmpdir, git_repo, replace)
    target = _target(tmpdir)
    url = str(target)
    start, end = action_logs(
        'project push',
        'chimera.commands.project.push.push',
        {'url': url, 'dry': False, 'project': None},
    )
    pushed = {
        'level': 'INFO',
        'url': url,
        'branch': 'main',
        'sha': Git(git_repo.path).rev_parse('main', short=False),
        'message': 'project push: pushed',
    }
    command.run('project', 'push', url).check(
        output=f'Pushed main to {url} (origin)',
        logging=[start, pushed, end],
    )
    compare(Git(target).branches(), expected=['main'])


def test_project_push_cli_dry_previews(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    _cli_project(tmpdir, git_repo, replace)
    target = _target(tmpdir)
    url = str(target)
    command.run('project', 'push', url, '--dry').check(
        output=f'Would push main to {url} (origin)',
        logging=action_logs(
            'project push',
            'chimera.commands.project.push.push',
            {'url': url, 'dry': True, 'project': None},
        ),
    )
    compare(Git(target).branches(), expected=[])  # untouched
    assert not Git(git_repo.path)('remote').strip()
