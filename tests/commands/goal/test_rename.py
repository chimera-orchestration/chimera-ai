import os
from pathlib import Path

import pytest
from giterator import Git
from giterator.testing import Repo
from testfixtures import LogCapture, Replacer, ShouldRaise, TempDir, compare
from testfixtures.loguru import LoguruSource

from chimera.agents import AgentSession
from chimera.commands.agent import live
from chimera.commands.goal.rename import RenameResult, rename
from chimera.commands.worktree import rm as worktree_rm
from chimera.commands.worktree.add import add
from chimera.config import UserError
from tests.cli import Command, action_logs


@pytest.fixture(autouse=True)
def _no_agents(replace: Replacer) -> None:
    replace.in_module(live, lambda worktree: [], module=worktree_rm)


def _goal(tmpdir: TempDir, repo: Repo, actors: tuple[str, ...] | None = None) -> Path:
    worktrees = tmpdir / 'worktrees'
    add(repo.path, worktrees, goal='g', actors=actors)
    return worktrees


def _rev(repo_path: Path, ref: str) -> str:
    return Git(repo_path).rev_parse(ref, short=False)


class TestRename:
    def test_renames_branch_and_moves_worktree(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = _goal(tmpdir, git_repo)
        compare(
            rename(git_repo.path, worktrees, 'g', 'h'),
            expected=RenameResult(
                branches=[('g/agent', 'h/agent')],
                worktrees=[(worktrees / 'g@agent', worktrees / 'h@agent')],
                warnings=[],
                cwd_moved_to=None,
            ),
        )
        compare(Git(git_repo.path).branches(), expected=['h/agent', 'main'])
        tmpdir.compare(['h@agent'], path='worktrees', recursive=False)
        # the moved worktree is still checked out on the (renamed) branch
        compare(
            Git(worktrees / 'h@agent')('rev-parse', '--abbrev-ref', 'HEAD').strip(),
            expected='h/agent',
        )
        # and git's own repo-side registration points at the moved path
        assert (
            f'worktree {(worktrees / "h@agent").resolve()}'
            in git_repo('worktree', 'list', '--porcelain').splitlines()
        )

    def test_renames_every_actor(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = _goal(tmpdir, git_repo, actors=('human', 'agent'))
        result = rename(git_repo.path, worktrees, 'g', 'h')
        compare(result.branches, expected=[('g/agent', 'h/agent'), ('g/human', 'h/human')])
        compare(result.worktrees, expected=[(worktrees / 'g@agent', worktrees / 'h@agent')])
        compare(Git(git_repo.path).branches(), expected=['h/agent', 'h/human', 'main'])
        tmpdir.compare(['h@agent'], path='worktrees', recursive=False)

    def test_preserves_uncommitted_work(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = _goal(tmpdir, git_repo)
        (worktrees / 'g@agent' / 'wip.txt').write_text('unsaved')
        rename(git_repo.path, worktrees, 'g', 'h')
        compare((worktrees / 'h@agent' / 'wip.txt').read_text(), expected='unsaved')

    def test_carries_branch_config(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = _goal(tmpdir, git_repo)
        git_repo('config', 'branch.g/agent.description', 'the goal')
        rename(git_repo.path, worktrees, 'g', 'h')
        # git branch -m migrates the branch.<name>.* config section along with the ref
        compare(git_repo('config', 'branch.h/agent.description').strip(), expected='the goal')

    def test_completes_a_half_done_rename(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = _goal(tmpdir, git_repo)
        git_repo('branch', '-m', 'g/agent', 'h/agent')  # branch moved, worktree didn't
        result = rename(git_repo.path, worktrees, 'g', 'h')
        compare(result.branches, expected=[])
        compare(result.worktrees, expected=[(worktrees / 'g@agent', worktrees / 'h@agent')])
        tmpdir.compare(['h@agent'], path='worktrees', recursive=False)

    def test_reports_where_a_cwd_inside_the_worktree_moved_to(
        self, tmpdir: TempDir, git_repo: Repo
    ) -> None:
        worktrees = _goal(tmpdir, git_repo)
        sub = tmpdir.makedir('worktrees/g@agent/sub')
        result = rename(git_repo.path, worktrees, 'g', 'h', cwd=sub)
        compare(result.cwd_moved_to, expected=worktrees / 'h@agent' / 'sub')

    def test_cwd_outside_the_goal_is_untouched(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = _goal(tmpdir, git_repo)
        result = rename(git_repo.path, worktrees, 'g', 'h', cwd=tmpdir.path)
        assert result.cwd_moved_to is None

    def test_warns_about_an_unregistered_worktree_dir(
        self, tmpdir: TempDir, git_repo: Repo
    ) -> None:
        worktrees = _goal(tmpdir, git_repo)
        stray = tmpdir.makedir('worktrees/g@reviewer')  # a dir git doesn't know about
        result = rename(git_repo.path, worktrees, 'g', 'h')
        compare(
            result.warnings,
            expected=[f'{stray} is not a registered worktree — left in place (see ch doctor)'],
        )
        tmpdir.compare(['g@reviewer', 'h@agent'], path='worktrees', recursive=False)

    def test_logs_the_renamed_refs_and_moved_worktrees(
        self, tmpdir: TempDir, git_repo: Repo
    ) -> None:
        worktrees = _goal(tmpdir, git_repo)
        tip = _rev(git_repo.path, 'main')  # the agent branch starts at main
        with LogCapture(LoguruSource(('message', 'extra'), level='INFO')) as log:
            rename(git_repo.path, worktrees, 'g', 'h')
        log.check(
            (
                'goal rename: refs',
                {
                    'goal': 'g',
                    'renamed_to': 'h',
                    'git': {'before': {'g/agent': tip}, 'after': {'h/agent': tip}},
                },
            ),
            (
                'goal rename: worktrees',
                {'moved': {str(worktrees / 'g@agent'): str(worktrees / 'h@agent')}},
            ),
        )


class TestSyncState:
    def test_renames_watermark_refs(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = _goal(tmpdir, git_repo)
        tip = _rev(git_repo.path, 'main')
        git_repo('update-ref', 'refs/chimera/synced/g/human', tip)
        rename(git_repo.path, worktrees, 'g', 'h')
        compare(
            git_repo(
                'for-each-ref', '--format=%(refname) %(objectname)', 'refs/chimera/synced/'
            ).strip(),
            expected=f'refs/chimera/synced/h/human {tip}',
        )

    def test_renames_append_markers(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = _goal(tmpdir, git_repo)
        tmpdir.write('repo/.git/chimera/appending/g@human', 'before=x\ntarget=y\n')
        rename(git_repo.path, worktrees, 'g', 'h')
        tmpdir.compare(['h@human'], path='repo/.git/chimera/appending')
        compare(
            (tmpdir / 'repo/.git/chimera/appending/h@human').read_text(),
            expected='before=x\ntarget=y\n',
        )


class TestRemotes:
    def test_warns_about_a_remote_branch_but_leaves_it(
        self, tmpdir: TempDir, git_repo: Repo
    ) -> None:
        worktrees = _goal(tmpdir, git_repo)
        tip = _rev(git_repo.path, 'main')
        git_repo('remote', 'add', 'origin', str(tmpdir / 'elsewhere'))
        git_repo('update-ref', 'refs/remotes/origin/g/agent', tip)
        result = rename(git_repo.path, worktrees, 'g', 'h')
        compare(
            result.warnings,
            expected=[
                'remote branch origin/g/agent keeps the old name — '
                'rename or delete it on the remote yourself'
            ],
        )
        compare(_rev(git_repo.path, 'refs/remotes/origin/g/agent'), expected=tip)

    def test_no_warning_without_a_remote_branch(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = _goal(tmpdir, git_repo)
        git_repo('remote', 'add', 'origin', str(tmpdir / 'elsewhere'))
        compare(rename(git_repo.path, worktrees, 'g', 'h').warnings, expected=[])

    def test_warns_about_an_upstream_still_tracking_the_old_name(
        self, tmpdir: TempDir, git_repo: Repo
    ) -> None:
        worktrees = _goal(tmpdir, git_repo)
        git_repo('config', 'branch.g/agent.remote', 'origin')
        git_repo('config', 'branch.g/agent.merge', 'refs/heads/g/agent')
        result = rename(git_repo.path, worktrees, 'g', 'h')
        # branch -m carries the config section, so the upstream survives — naming the old ref
        compare(
            git_repo('config', '--get', 'branch.h/agent.merge').strip(),
            expected='refs/heads/g/agent',
        )
        compare(
            result.warnings,
            expected=[
                'h/agent upstream still tracks g/agent on the remote — once renamed '
                'there: git branch -u <remote>/h/agent h/agent'
            ],
        )

    def test_an_upstream_elsewhere_is_not_flagged(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = _goal(tmpdir, git_repo)
        # a review goal's upstream is the PR ref, not the goal branch — untouched by the rename
        git_repo('config', 'branch.g/agent.remote', 'origin')
        git_repo('config', 'branch.g/agent.merge', 'refs/pull/5/head')
        compare(rename(git_repo.path, worktrees, 'g', 'h').warnings, expected=[])


class TestRefusals:
    def test_no_such_goal(self, tmpdir: TempDir, git_repo: Repo) -> None:
        with ShouldRaise(UserError("no goal 'ghost' to rename")):
            rename(git_repo.path, tmpdir / 'worktrees', 'ghost', 'h')

    def test_same_name(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = _goal(tmpdir, git_repo)
        with ShouldRaise(UserError("new name 'g' is the same as the old")):
            rename(git_repo.path, worktrees, 'g', 'g')

    def test_separator_in_new_name(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = _goal(tmpdir, git_repo)
        with ShouldRaise(
            UserError("'h@x' is not a valid goal name: '@' separates goal from actor")
        ):
            rename(git_repo.path, worktrees, 'g', 'h@x')

    def test_name_git_refuses(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = _goal(tmpdir, git_repo)
        with ShouldRaise(UserError("'bad..name' is not a valid goal name")):
            rename(git_repo.path, worktrees, 'g', 'bad..name')

    def test_path_separator_in_new_name(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = _goal(tmpdir, git_repo)
        with ShouldRaise(
            UserError(
                "'a/b' is not a valid goal name: no path separators — "
                "goal names are single path segments, like 'feature-x' or 'pr-123'"
            )
        ):
            rename(git_repo.path, worktrees, 'g', 'a/b')

    def test_bare_branch_blocks_the_new_namespace(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = _goal(tmpdir, git_repo)
        git_repo('branch', 'h')
        with ShouldRaise(
            UserError(
                "branch 'h' exists — git cannot hold refs/heads/h "
                'beside refs/heads/h/<actor> (ch goal adopt h?)'
            )
        ):
            rename(git_repo.path, worktrees, 'g', 'h')

    def test_branch_collision(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = _goal(tmpdir, git_repo)
        git_repo('branch', 'h/agent')
        with ShouldRaise(UserError('branch h/agent already exists')):
            rename(git_repo.path, worktrees, 'g', 'h')

    def test_worktree_collision(self, tmpdir: TempDir, git_repo: Repo) -> None:
        worktrees = _goal(tmpdir, git_repo)
        taken = tmpdir.makedir('worktrees/h@agent')
        with ShouldRaise(UserError(f'{taken} already exists')):
            rename(git_repo.path, worktrees, 'g', 'h')

    def test_refuses_while_an_agent_is_live(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer
    ) -> None:
        worktrees = _goal(tmpdir, git_repo)
        replace.in_module(
            live,
            lambda worktree: [AgentSession('x', 'x', 'idle', worktree, None, pid=4242)],
            module=worktree_rm,
        )
        with ShouldRaise(
            UserError(
                f'an agent is live in {worktrees / "g@agent"}: pid 4242  idle\n'
                'find its terminal or kill the pid, then re-run'
            )
        ):
            rename(git_repo.path, worktrees, 'g', 'h')
        tmpdir.compare(['g@agent'], path='worktrees', recursive=False)  # nothing moved


GR = 'chimera.commands.goal.rename.rename'


def _project(tmpdir: TempDir, repo: Repo) -> Path:
    tmpdir.dump('config.yaml', {'kind': 'project', 'repo': str(repo.path)})
    return tmpdir.path


class TestCli:
    def test_goal_rename(self, tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
        project = _project(tmpdir, git_repo)
        command.run('worktree', 'add', '--goal', 'g')
        tip = _rev(git_repo.path, 'g/agent')
        old_wt = (project / 'worktrees' / 'g@agent').resolve()
        new_wt = (project / 'worktrees' / 'h@agent').resolve()
        start, end = action_logs('goal rename', GR, {'old': 'g', 'new': 'h', 'project': None})
        command.run('goal', 'rename', 'g', 'h').check(
            output=f'Renamed branch g/agent to h/agent\nMoved {old_wt} to {new_wt}',
            logging=[
                start,
                {
                    'level': 'INFO',
                    'goal': 'g',
                    'renamed_to': 'h',
                    'git': {'before': {'g/agent': tip}, 'after': {'h/agent': tip}},
                    'message': 'goal rename: refs',
                },
                {
                    'level': 'INFO',
                    'moved': {str(old_wt): str(new_wt)},
                    'message': 'goal rename: worktrees',
                },
                end,
            ],
        )
        compare(Git(git_repo.path).branches(), expected=['h/agent', 'main'])
        tmpdir.compare(['h@agent'], path='worktrees', recursive=False)

    def test_goal_rename_warns_and_points_a_moved_cwd_home(
        self, tmpdir: TempDir, git_repo: Repo, command: Command
    ) -> None:
        project = _project(tmpdir, git_repo)
        command.run('worktree', 'add', '--goal', 'g')
        tip = _rev(git_repo.path, 'g/agent')
        git_repo('remote', 'add', 'origin', str(tmpdir / 'elsewhere'))
        git_repo('update-ref', 'refs/remotes/origin/g/agent', tip)
        old_wt = (project / 'worktrees' / 'g@agent').resolve()
        new_wt = (project / 'worktrees' / 'h@agent').resolve()
        os.chdir(old_wt)  # the rename moves the worktree out from under us
        warning = (
            'remote branch origin/g/agent keeps the old name — '
            'rename or delete it on the remote yourself'
        )
        start, end = action_logs('goal rename', GR, {'old': 'g', 'new': 'h', 'project': None})
        command.run('goal', 'rename', 'g', 'h').check(
            output=f'Renamed branch g/agent to h/agent\nMoved {old_wt} to {new_wt}\n'
            f'warning: {warning}\nnote: your cwd moved — cd {new_wt}',
            logging=[
                start,
                {
                    'level': 'INFO',
                    'goal': 'g',
                    'renamed_to': 'h',
                    'git': {'before': {'g/agent': tip}, 'after': {'h/agent': tip}},
                    'message': 'goal rename: refs',
                },
                {
                    'level': 'INFO',
                    'moved': {str(old_wt): str(new_wt)},
                    'message': 'goal rename: worktrees',
                },
                {'level': 'WARNING', 'warnings': [warning], 'message': 'goal rename: warnings'},
                end,
            ],
        )

    def test_goal_rename_collision_is_a_user_error(
        self, tmpdir: TempDir, git_repo: Repo, command: Command
    ) -> None:
        _project(tmpdir, git_repo)
        command.run('worktree', 'add', '--goal', 'g')
        git_repo('branch', 'h/agent')
        command.run('goal', 'rename', 'g', 'h').check(
            output='Error: branch h/agent already exists',
            return_code=1,
            logging=action_logs(
                'goal rename',
                GR,
                {'old': 'g', 'new': 'h', 'project': None},
                error='UserError: branch h/agent already exists',
            ),
        )
