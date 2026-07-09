import pytest
from giterator import Git
from testfixtures import LogCapture, Replacer, ShouldRaise, TempDir, compare
from testfixtures.loguru import LoguruSource

from chimera.commands.project.new import SEED_MESSAGE, new
from chimera.commands.worktree.add import add as worktree_add
from chimera.config import UserError
from tests.cli import Command, action_logs


@pytest.fixture(autouse=True)
def _git_identity(replace: Replacer) -> None:
    replace.in_environ('GIT_AUTHOR_NAME', 'Test Author')
    replace.in_environ('GIT_AUTHOR_EMAIL', 'author@example.com')
    replace.in_environ('GIT_COMMITTER_NAME', 'Test Committer')
    replace.in_environ('GIT_COMMITTER_EMAIL', 'committer@example.com')


def test_new_creates_a_bare_repo_project(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    compare(new(workspace, 'proj'), expected=workspace / 'proj')
    repo = workspace / 'proj' / 'repo'
    git = Git(repo)
    compare(git('rev-parse', '--is-bare-repository').strip(), expected='true')  # no working tree
    compare(git.branches(), expected=['main'])
    compare(tmpdir.parse('lycia/proj/config.yaml'), expected={'kind': 'project', 'repo': str(repo)})
    tmpdir.compare(
        ['config.yaml', 'knowledge', 'principles', 'processes', 'prompts', 'repo', 'roles'],
        path='lycia/proj',
        recursive=False,
    )


def test_new_seeds_one_empty_commit_with_the_users_identity(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    new(workspace, 'proj')
    git = Git(workspace / 'proj' / 'repo')
    compare(git('rev-list', '--count', 'main').strip(), expected='1')
    compare(git('log', '--format=%s', 'main').strip(), expected=SEED_MESSAGE)
    compare(git('ls-tree', 'main').strip(), expected='')  # empty tree: no README or other content
    # the user's own identity, resolved as git normally would — never a synthetic chimera one
    compare(
        git('log', '--format=%an <%ae> %cn <%ce>', 'main').strip(),
        expected='Test Author <author@example.com> Test Committer <committer@example.com>',
    )


def test_new_repo_supports_a_first_goal_worktree(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    project = new(workspace, 'proj')
    repo = project / 'repo'
    worktree_add(repo, project / 'worktrees', goal='g')
    assert (project / 'worktrees' / 'g@agent').is_dir()
    compare(Git(repo).branches(), expected=['g/agent', 'main'])


def test_new_checkout_stands_up_a_plain_worktree_of_main(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    checkout = tmpdir / 'checkout'
    new(workspace, 'proj', checkout=checkout)
    compare(Git(checkout)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='main')
    tmpdir.compare(['.git'], path='checkout', recursive=False)  # empty tree, just the git link


def test_new_refuses_when_the_project_already_exists(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    project = new(workspace, 'proj')
    with ShouldRaise(UserError(f'project proj already exists at {project}')):
        new(workspace, 'proj')


def test_new_refuses_over_a_tracked_project_without_a_repo(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(tmpdir / 'elsewhere')})
    with ShouldRaise(UserError(f'project proj already exists at {workspace / "proj"}')):
        new(workspace, 'proj')


def test_new_logs_the_seed_ref_before_and_after(tmpdir: TempDir) -> None:
    workspace = tmpdir.makedir('lycia')
    with LogCapture(LoguruSource(('message', 'extra'), level='INFO')) as log:
        new(workspace, 'proj')
    sha = Git(workspace / 'proj' / 'repo').rev_parse('main', short=False)
    log.check(
        ('project new: refs', {'git': {'before': {}, 'after': {'main': sha}}}),
    )


def _new_logs(sha: str, *, checkout: str | None = None) -> list[dict[str, object]]:
    """start / `project new: refs` event / end for creating the `proj` project."""
    start, end = action_logs(
        'project new', 'chimera.commands.project.new.new', {'name': 'proj', 'checkout': checkout}
    )
    event = {
        'level': 'INFO',
        'git': {'before': {}, 'after': {'main': sha}},
        'message': 'project new: refs',
    }
    return [start, event, end]


def test_project_new_cli(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    workspace = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    replace.in_environ('CHIMERA_WORKSPACE', str(workspace))
    run = command.run('project', 'new', 'proj')
    sha = Git(workspace / 'proj' / 'repo').rev_parse('main', short=False)
    run.check(output=f'Created {workspace / "proj"}', logging=_new_logs(sha))


def test_project_new_checkout_cli(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    workspace = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    replace.in_environ('CHIMERA_WORKSPACE', str(workspace))
    checkout = tmpdir / 'checkout'
    run = command.run('project', 'new', 'proj', '--checkout', str(checkout))
    sha = Git(workspace / 'proj' / 'repo').rev_parse('main', short=False)
    run.check(
        output=f'Created {workspace / "proj"}\nChecked out at {checkout}',
        logging=_new_logs(sha, checkout=str(checkout)),
    )
    compare(Git(checkout)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='main')
