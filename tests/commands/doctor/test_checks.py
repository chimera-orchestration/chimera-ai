import shutil

import yaml
from giterator import Git
from giterator.testing import Repo
from testfixtures import LogCapture, Replacer, TempDir, compare, not_there
from testfixtures.loguru import LoguruSource

from chimera.commands.doctor import checks as doctor_checks
from chimera.commands.doctor.checks import (
    ChimeraUpToDateCheck,
    GitignoreCheck,
    LegacyWorktreeSeparatorCheck,
    OrphanedWorktreeCheck,
    ProjectConfigCheck,
    ShellCompletionCheck,
    StaleHumanWorktreeCheck,
    WorkspaceConfigCheck,
    WorkspaceEnvCheck,
    WorktreeBranchCheck,
)
from chimera.commands.doctor.core import Check, Finding
from chimera.commands.init import TEMPLATE
from chimera.worktrees import registered_worktrees


def _ws(tmpdir: TempDir):
    ws = tmpdir.makedir('lycia')
    (ws / 'processes').mkdir()
    shutil.copy(TEMPLATE / '.gitignore', ws / '.gitignore')  # a healthy workspace
    return ws


def _project(tmpdir, ws, repo_path, *, name='proj', kind=None):
    project = ws / name
    data = {} if kind is None else {'kind': kind}
    data['repo'] = str(repo_path)
    tmpdir.dump(project / 'config.yaml', data)
    (project / 'worktrees').mkdir()
    return project


def _human_worktree(repo, project, goal, *, ahead=False, dirty=False):
    worktree = project / 'worktrees' / f'{goal}-human'
    Git(repo.path)('worktree', 'add', '-b', f'{goal}/human', str(worktree), 'main')
    if ahead:
        Repo(worktree).commit_content('work')
    if dirty:
        (worktree / 'scratch.txt').write_text('wip')
    return worktree


def _run(check: Check, ws, fix: bool = False) -> list[Finding]:
    return list(check.run(ws, fix))


def _config(path):
    return yaml.safe_load((path / 'config.yaml').read_text())


def _env_finding(ws, where: str) -> Finding:
    """The workspace-env finding text — where: the '… is not set'/'… not this workspace' clause."""
    return Finding(
        'workspace-env',
        f'$CHIMERA_WORKSPACE {where} — '
        'add to your shell profile (~/.zshrc, ~/.bashrc, ~/.profile):\n'
        f'    export CHIMERA_WORKSPACE="{ws}"',
        False,
        False,
    )


class TestWorkspaceConfig:
    def test_missing_reports_without_writing(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        compare(
            _run(WorkspaceConfigCheck(), ws),
            expected=[Finding('workspace-config', f'{ws}/config.yaml missing', False, True)],
        )
        assert (ws / 'config.yaml').exists() is False  # report only, nothing written

    def test_missing_fixed(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        compare(
            _run(WorkspaceConfigCheck(), ws, fix=True),
            expected=[Finding('workspace-config', f'{ws}/config.yaml missing', True, True)],
        )
        compare(_config(ws), expected={'kind': 'workspace'})

    def test_legacy_keeps_other_keys(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        tmpdir.dump('lycia/config.yaml', {'name': 'lycia'})
        compare(
            _run(WorkspaceConfigCheck(), ws, fix=True),
            expected=[
                Finding('workspace-config', f'{ws}/config.yaml missing kind: workspace', True, True)
            ],
        )
        compare(_config(ws), expected={'kind': 'workspace', 'name': 'lycia'})

    def test_already_current_is_silent(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
        compare(_run(WorkspaceConfigCheck(), ws), expected=[])

    def test_wrong_kind_not_fixable(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        tmpdir.dump('lycia/config.yaml', {'kind': 'project'})
        compare(
            _run(WorkspaceConfigCheck(), ws, fix=True),
            expected=[
                Finding(
                    'workspace-config',
                    f'{ws}/config.yaml has kind: project at the workspace root',
                    False,
                    False,
                )
            ],
        )
        compare(_config(ws), expected={'kind': 'project'})  # left untouched

    def test_with_repo_is_not_stamped(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        tmpdir.dump('lycia/config.yaml', {'repo': '/some/repo'})  # a project config, not a root
        compare(
            _run(WorkspaceConfigCheck(), ws, fix=True),
            expected=[
                Finding(
                    'workspace-config',
                    f'{ws}/config.yaml looks like a project (has repo:), not a workspace root',
                    False,
                    False,
                )
            ],
        )
        compare(_config(ws), expected={'repo': '/some/repo'})  # never gets kind: workspace


class TestGitignore:
    def test_current_is_silent(self, tmpdir: TempDir) -> None:
        compare(_run(GitignoreCheck(), _ws(tmpdir)), expected=[])

    def test_missing_entry_reported(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        (ws / '.gitignore').write_text('*.lock\nservices-running.jsonl\n*/repo/\n*/worktrees/\n')
        compare(
            _run(GitignoreCheck(), ws),
            expected=[Finding('gitignore', f"{ws / '.gitignore'} missing 'logs/'", False, True)],
        )

    def test_missing_entry_appended_after_an_unterminated_final_line(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        (ws / '.gitignore').write_text('*.lock\nservices-running.jsonl\n*/repo/\n*/worktrees/')
        compare(
            _run(GitignoreCheck(), ws, fix=True),
            expected=[Finding('gitignore', f"{ws / '.gitignore'} missing 'logs/'", True, True)],
        )
        compare(
            (ws / '.gitignore').read_text(),
            expected='*.lock\nservices-running.jsonl\n*/repo/\n*/worktrees/\nlogs/\n',
        )

    def test_keeps_unrelated_custom_entries(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        (ws / '.gitignore').write_text('.DS_Store\n')  # the user's own, none of ours
        _run(GitignoreCheck(), ws, fix=True)
        compare(
            (ws / '.gitignore').read_text(),
            expected='.DS_Store\n*.lock\nservices-running.jsonl\nlogs/\n*/repo/\n*/worktrees/\n',
        )

    def test_absent_file_created(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        (ws / '.gitignore').unlink()
        compare(
            _run(GitignoreCheck(), ws, fix=True),
            expected=[
                Finding('gitignore', f'{ws / ".gitignore"} missing {entry!r}', True, True)
                for entry in (
                    '*.lock',
                    'services-running.jsonl',
                    'logs/',
                    '*/repo/',
                    '*/worktrees/',
                )
            ],
        )
        compare(
            (ws / '.gitignore').read_text(),
            expected='*.lock\nservices-running.jsonl\nlogs/\n*/repo/\n*/worktrees/\n',
        )


class TestWorkspaceEnv:
    def test_unset_reports_export_line(self, tmpdir: TempDir, replace: Replacer) -> None:
        ws = _ws(tmpdir)
        replace.in_environ('CHIMERA_WORKSPACE', not_there)
        compare(_run(WorkspaceEnvCheck(), ws), expected=[_env_finding(ws, 'is not set')])

    def test_set_to_this_workspace_is_silent(self, tmpdir: TempDir, replace: Replacer) -> None:
        ws = _ws(tmpdir)
        replace.in_environ('CHIMERA_WORKSPACE', str(ws))
        compare(_run(WorkspaceEnvCheck(), ws), expected=[])

    def test_pointing_elsewhere_reported(self, tmpdir: TempDir, replace: Replacer) -> None:
        ws = _ws(tmpdir)
        other = tmpdir.makedir('other')
        replace.in_environ('CHIMERA_WORKSPACE', str(other))
        compare(
            _run(WorkspaceEnvCheck(), ws),
            expected=[_env_finding(ws, f'is {other}, not this workspace')],
        )


class TestProjectConfig:
    def test_legacy_upgraded(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        compare(
            _run(ProjectConfigCheck(), ws, fix=True),
            expected=[
                Finding(
                    'project-config', f'{project}/config.yaml missing kind: project', True, True
                )
            ],
        )
        compare(_config(project), expected={'kind': 'project', 'repo': str(git_repo.path)})

    def test_already_current_is_silent(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        _project(tmpdir, ws, git_repo.path, kind='project')
        compare(_run(ProjectConfigCheck(), ws), expected=[])

    def test_wrong_kind_with_repo_fixed(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(
            tmpdir, ws, git_repo.path, kind='workspace'
        )  # repo: proves it's a project
        compare(
            _run(ProjectConfigCheck(), ws, fix=True),
            expected=[
                Finding(
                    'project-config',
                    f'{project}/config.yaml has kind: workspace but repo: marks it a project',
                    True,
                    True,
                )
            ],
        )
        compare(_config(project), expected={'kind': 'project', 'repo': str(git_repo.path)})

    def test_unexpected_kind_without_repo_not_fixable(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        project = ws / 'weird'
        tmpdir.dump('lycia/weird/config.yaml', {'kind': 'bogus'})  # no repo to disambiguate
        compare(
            _run(ProjectConfigCheck(), ws, fix=True),
            expected=[
                Finding(
                    'project-config',
                    f'{project}/config.yaml has unexpected kind: bogus',
                    False,
                    False,
                )
            ],
        )

    def test_no_kind_no_repo_not_fixable(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        project = ws / 'weird'
        tmpdir.dump('lycia/weird/config.yaml', {'something': 'else'})
        compare(
            _run(ProjectConfigCheck(), ws),
            expected=[
                Finding(
                    'project-config', f'{project}/config.yaml has no kind and no repo', False, False
                )
            ],
        )


class TestStaleHumanWorktree:
    def test_clean_removed_branch_survives(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = _human_worktree(git_repo, project, 'g1')
        compare(
            _run(StaleHumanWorktreeCheck(), ws, fix=True),
            expected=[Finding('human-worktrees', f'stale human worktree {worktree}', True, True)],
        )
        tmpdir.compare(path='lycia/proj/worktrees', expected=())
        compare(Git(git_repo.path).branches(), expected=['g1/human', 'main'])

    def test_clean_report_only_leaves_it(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = _human_worktree(git_repo, project, 'g1')
        compare(
            _run(StaleHumanWorktreeCheck(), ws),
            expected=[Finding('human-worktrees', f'stale human worktree {worktree}', False, True)],
        )
        tmpdir.compare(['g1-human'], path='lycia/proj/worktrees', recursive=False)

    def test_dirty_left_in_place(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = _human_worktree(git_repo, project, 'g2', dirty=True)
        compare(
            _run(StaleHumanWorktreeCheck(), ws, fix=True),
            expected=[
                Finding(
                    'human-worktrees',
                    f'{worktree} has uncommitted changes — left in place',
                    False,
                    False,
                )
            ],
        )
        tmpdir.compare(['g2-human'], path='lycia/proj/worktrees', recursive=False)

    def test_unmerged_left_in_place(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = _human_worktree(git_repo, project, 'g3', ahead=True)
        compare(
            _run(StaleHumanWorktreeCheck(), ws, fix=True),
            expected=[
                Finding(
                    'human-worktrees',
                    f'{worktree} has unmerged commits — left in place',
                    False,
                    False,
                )
            ],
        )
        tmpdir.compare(['g3-human'], path='lycia/proj/worktrees', recursive=False)

    def test_skips_project_without_a_live_repo(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        tmpdir.dump('lycia/ref/config.yaml', {'kind': 'project'})  # no repo
        compare(_run(StaleHumanWorktreeCheck(), ws), expected=[])

    def test_ignores_unregistered_dir(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        (project / 'worktrees' / 'leftover-human').mkdir()  # looks human, not a worktree
        compare(_run(StaleHumanWorktreeCheck(), ws, fix=True), expected=[])


def _legacy_worktree(repo, project, goal, actor='agent', *, dirty=False):
    worktree = project / 'worktrees' / f'{goal}-{actor}'  # old dash-joined dir name
    Git(repo.path)('worktree', 'add', '-b', f'{goal}/{actor}', str(worktree), 'main')
    if dirty:
        (worktree / 'scratch.txt').write_text('wip')
    return worktree


class TestLegacyWorktreeSeparator:
    def test_migrated(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        _legacy_worktree(git_repo, project, 'my-goal')
        compare(
            _run(LegacyWorktreeSeparatorCheck(), ws, fix=True),
            expected=[
                Finding(
                    'worktree-separator',
                    'legacy worktree my-goal-agent → my-goal@agent',
                    True,
                    True,
                )
            ],
        )
        canonical = (project / 'worktrees' / 'my-goal@agent').resolve()
        tmpdir.compare(['my-goal@agent'], path='lycia/proj/worktrees', recursive=False)  # renamed
        compare(
            registered_worktrees(Git(git_repo.path)),
            expected={git_repo.path.resolve(), canonical},
        )

    def test_report_only_leaves_it(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        _legacy_worktree(git_repo, project, 'my-goal')
        compare(
            _run(LegacyWorktreeSeparatorCheck(), ws),
            expected=[
                Finding(
                    'worktree-separator',
                    'legacy worktree my-goal-agent → my-goal@agent',
                    False,
                    True,
                )
            ],
        )
        tmpdir.compare(
            ['my-goal-agent'], path='lycia/proj/worktrees', recursive=False
        )  # left as-is

    def test_preserves_uncommitted_work(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        _legacy_worktree(git_repo, project, 'my-goal', dirty=True)
        _run(LegacyWorktreeSeparatorCheck(), ws, fix=True)
        compare(
            (project / 'worktrees' / 'my-goal@agent' / 'scratch.txt').read_text(), expected='wip'
        )

    def test_migrates_non_human_actors(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        _legacy_worktree(git_repo, project, 'g', actor='reviewer')
        compare(
            _run(LegacyWorktreeSeparatorCheck(), ws, fix=True),
            expected=[
                Finding('worktree-separator', 'legacy worktree g-reviewer → g@reviewer', True, True)
            ],
        )
        tmpdir.compare(['g@reviewer'], path='lycia/proj/worktrees', recursive=False)

    def test_ignores_human_worktrees(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        _human_worktree(git_repo, project, 'g')  # the human-worktrees check owns these
        compare(_run(LegacyWorktreeSeparatorCheck(), ws), expected=[])

    def test_skips_already_canonical(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = project / 'worktrees' / 'g@agent'
        Git(git_repo.path)('worktree', 'add', '-b', 'g/agent', str(worktree), 'main')
        compare(_run(LegacyWorktreeSeparatorCheck(), ws, fix=True), expected=[])

    def test_skips_project_without_a_live_repo(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, ws / 'proj' / 'repo')  # repo path doesn't exist
        (project / 'worktrees' / 'g-agent').mkdir()
        compare(_run(LegacyWorktreeSeparatorCheck(), ws), expected=[])

    def test_ignores_non_goal_actor_branch(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = project / 'worktrees' / 'sidequest'
        Git(git_repo.path)(
            'worktree', 'add', '-b', 'sidequest', str(worktree), 'main'
        )  # no goal/actor
        compare(_run(LegacyWorktreeSeparatorCheck(), ws, fix=True), expected=[])

    def test_skips_stale_registration(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = _legacy_worktree(git_repo, project, 'g')
        shutil.rmtree(worktree)  # registered but the dir is gone — can't read its branch
        compare(_run(LegacyWorktreeSeparatorCheck(), ws), expected=[])


def _agent_worktree(repo, project, goal, actor='agent'):
    worktree = project / 'worktrees' / f'{goal}@{actor}'
    Git(repo.path)('worktree', 'add', '-b', f'{goal}/{actor}', str(worktree), 'main')
    return worktree.resolve()  # the check reports the resolved path (from registered_worktrees)


def _flip(worktree, to='stray'):
    Git(worktree)('checkout', '-b', to)  # a GUI parks the worktree on the wrong branch


class TestWorktreeBranch:
    def test_correct_branch_is_silent(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        _agent_worktree(git_repo, project, 'g')
        compare(_run(WorktreeBranchCheck(), ws), expected=[])

    def test_wrong_branch_fixed(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = _agent_worktree(git_repo, project, 'g')
        _flip(worktree)
        seed = Git(git_repo.path).rev_parse('main', short=False)  # both branches sit at seed
        with LogCapture(LoguruSource(('message', 'extra'))) as log:
            compare(
                _run(WorktreeBranchCheck(), ws, fix=True),
                expected=[
                    Finding(
                        'worktree-branch', f'{worktree} is on stray, expected g/agent', True, True
                    )
                ],
            )
        compare(Git(worktree)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='g/agent')
        log.check(
            (
                'worktree-branch: refs',
                {
                    'worktree': str(worktree),
                    'git': {'before': {'stray': seed}, 'after': {'g/agent': seed}},
                },
            )
        )

    def test_wrong_branch_report_only(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = _agent_worktree(git_repo, project, 'g')
        _flip(worktree)
        compare(
            _run(WorktreeBranchCheck(), ws),
            expected=[
                Finding('worktree-branch', f'{worktree} is on stray, expected g/agent', False, True)
            ],
        )
        compare(Git(worktree)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='stray')

    def test_dirty_left_in_place(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = _agent_worktree(git_repo, project, 'g')
        _flip(worktree)
        (worktree / 'scratch.txt').write_text('wip')
        compare(
            _run(WorktreeBranchCheck(), ws, fix=True),
            expected=[
                Finding(
                    'worktree-branch',
                    f'{worktree} is on stray, expected g/agent — uncommitted changes, left in place',
                    False,
                    False,
                )
            ],
        )
        compare(Git(worktree)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='stray')

    def test_missing_target_branch_not_fixable(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = _agent_worktree(git_repo, project, 'g')
        _flip(worktree)
        Git(git_repo.path)('branch', '-D', 'g/agent')  # nothing left to switch back to
        compare(
            _run(WorktreeBranchCheck(), ws, fix=True),
            expected=[
                Finding(
                    'worktree-branch',
                    f'{worktree} is on stray, but branch g/agent is gone',
                    False,
                    False,
                )
            ],
        )

    def test_detached_head_fixed(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = _agent_worktree(git_repo, project, 'g')
        Git(worktree)('checkout', '--detach')
        compare(
            _run(WorktreeBranchCheck(), ws, fix=True),
            expected=[
                Finding('worktree-branch', f'{worktree} is on HEAD, expected g/agent', True, True)
            ],
        )
        compare(Git(worktree)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='g/agent')

    def test_ignores_human_actor(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        _flip(_agent_worktree(git_repo, project, 'g', actor='human'))  # humans aren't policed
        compare(_run(WorktreeBranchCheck(), ws), expected=[])

    def test_ignores_dir_without_separator(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        Git(git_repo.path)(
            'worktree', 'add', '-b', 'sidequest', str(project / 'worktrees' / 'sidequest'), 'main'
        )
        compare(_run(WorktreeBranchCheck(), ws), expected=[])

    def test_skips_stale_registration(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = _agent_worktree(git_repo, project, 'g')
        _flip(worktree)
        shutil.rmtree(worktree)  # registered but gone — can't read its branch
        compare(_run(WorktreeBranchCheck(), ws), expected=[])

    def test_skips_project_without_a_live_repo(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        _project(tmpdir, ws, ws / 'proj' / 'repo')  # repo path doesn't exist
        compare(_run(WorktreeBranchCheck(), ws), expected=[])


class TestOrphanedWorktree:
    def test_registration_pruned(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = project / 'worktrees' / 'gone@agent'
        Git(git_repo.path)('worktree', 'add', '-b', 'gone/agent', str(worktree), 'main')
        shutil.rmtree(worktree)  # registration is now stale
        compare(
            _run(OrphanedWorktreeCheck(), ws, fix=True),
            expected=[
                Finding(
                    'orphaned-worktrees',
                    f'stale worktree registration for {worktree.resolve()}',
                    True,
                    True,
                )
            ],
        )
        compare(registered_worktrees(Git(git_repo.path)), expected={git_repo.path.resolve()})

    def test_registration_report_only_keeps_it(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = project / 'worktrees' / 'gone@agent'
        Git(git_repo.path)('worktree', 'add', '-b', 'gone/agent', str(worktree), 'main')
        shutil.rmtree(worktree)
        compare(
            _run(OrphanedWorktreeCheck(), ws),
            expected=[
                Finding(
                    'orphaned-worktrees',
                    f'stale worktree registration for {worktree.resolve()}',
                    False,
                    True,
                )
            ],
        )
        compare(
            registered_worktrees(Git(git_repo.path)),
            expected={git_repo.path.resolve(), worktree.resolve()},
        )

    def test_leftover_dir_reported(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        leftover = project / 'worktrees' / 'random'  # a dir that was never a git worktree
        leftover.mkdir()
        compare(
            _run(OrphanedWorktreeCheck(), ws),
            expected=[
                Finding(
                    'orphaned-worktrees', f'{leftover} is not a registered worktree', False, False
                )
            ],
        )

    def test_none_when_clean(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        _project(tmpdir, ws, git_repo.path)
        compare(_run(OrphanedWorktreeCheck(), ws), expected=[])

    def test_skips_project_without_a_live_repo(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        tmpdir.dump('lycia/ref/config.yaml', {'kind': 'project'})
        compare(_run(OrphanedWorktreeCheck(), ws), expected=[])


class TestChimeraRepoDiscovery:
    def test_finds_the_nearest_checkout(self, tmpdir: TempDir) -> None:
        repo = tmpdir.makedir('repo')
        (repo / '.git').mkdir()
        nested = tmpdir.makedir('repo/src/pkg')
        compare(doctor_checks._chimera_repo(nested / 'checks.py'), expected=repo.resolve())

    def test_none_without_a_checkout(self, tmpdir: TempDir) -> None:
        nested = tmpdir.makedir('a/b/c')
        compare(doctor_checks._chimera_repo(nested / 'checks.py'), expected=None)


def _chimera_clone(tmpdir: TempDir, replace: Replacer) -> tuple[Repo, Git]:
    """An origin repo and a clone wired together like a real dev checkout, patched in."""
    origin = Repo.make(tmpdir / 'origin')
    origin.commit_content('seed')
    local = Git.clone(origin.path, tmpdir / 'local')
    replace.in_module(doctor_checks._chimera_repo, lambda: local.path)
    return origin, local


class TestChimeraUpToDate:
    def test_no_checkout_is_silent(self, tmpdir: TempDir, replace: Replacer) -> None:
        replace.in_module(doctor_checks._chimera_repo, lambda: None)
        compare(_run(ChimeraUpToDateCheck(), _ws(tmpdir)), expected=[])

    def test_no_remote_is_silent(self, tmpdir: TempDir, replace: Replacer, git_repo: Repo) -> None:
        replace.in_module(doctor_checks._chimera_repo, lambda: git_repo.path)  # no origin at all
        compare(_run(ChimeraUpToDateCheck(), _ws(tmpdir)), expected=[])

    def test_up_to_date_no_deploy_is_silent(self, tmpdir: TempDir, replace: Replacer) -> None:
        _chimera_clone(tmpdir, replace)
        compare(_run(ChimeraUpToDateCheck(), _ws(tmpdir)), expected=[])

    def test_deploy_matching_main_is_silent(self, tmpdir: TempDir, replace: Replacer) -> None:
        _origin, local = _chimera_clone(tmpdir, replace)
        local('branch', 'deploy', 'main')
        compare(_run(ChimeraUpToDateCheck(), _ws(tmpdir)), expected=[])

    def test_main_behind_origin_reported(self, tmpdir: TempDir, replace: Replacer) -> None:
        origin, local = _chimera_clone(tmpdir, replace)
        origin.commit_content('remote-ahead')  # local hasn't fetched this yet
        compare(
            _run(ChimeraUpToDateCheck(), _ws(tmpdir)),
            expected=[
                Finding(
                    'chimera-up-to-date',
                    f'{local.path} main is not up to date with origin/main',
                    False,
                    False,
                )
            ],
        )
        # the check's own `git fetch` ran even without --fix — origin/main is now current
        compare(
            local.rev_parse('origin/main', short=False),
            expected=origin.rev_parse('main', short=False),
        )

    def test_main_behind_origin_is_never_auto_fixed(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        origin, local = _chimera_clone(tmpdir, replace)
        original = local.rev_parse('main', short=False)
        origin.commit_content('remote-ahead')
        _run(ChimeraUpToDateCheck(), _ws(tmpdir), fix=True)
        compare(local.rev_parse('main', short=False), expected=original)  # untouched

    def test_deploy_mismatch_report_only(self, tmpdir: TempDir, replace: Replacer) -> None:
        origin, local = _chimera_clone(tmpdir, replace)
        first = origin.rev_parse('main', short=False)
        origin.commit_content('second')
        local('fetch', 'origin')
        local('checkout', 'origin/main', '-B', 'main')  # local main now matches origin again
        local('branch', 'deploy', first)  # deploy lags behind
        compare(
            _run(ChimeraUpToDateCheck(), _ws(tmpdir)),
            expected=[
                Finding(
                    'chimera-up-to-date', f'{local.path} deploy does not point at main', False, True
                )
            ],
        )
        compare(local.rev_parse('deploy', short=False), expected=first)  # left in place

    def test_deploy_mismatch_fixed(self, tmpdir: TempDir, replace: Replacer) -> None:
        origin, local = _chimera_clone(tmpdir, replace)
        first = origin.rev_parse('main', short=False)
        second = origin.commit_content('second', short=False)
        local('fetch', 'origin')
        local('checkout', 'origin/main', '-B', 'main')
        local('branch', 'deploy', first)
        with LogCapture(LoguruSource(('message', 'extra'))) as log:
            compare(
                _run(ChimeraUpToDateCheck(), _ws(tmpdir), fix=True),
                expected=[
                    Finding(
                        'chimera-up-to-date', f'{local.path} deploy repointed to main', True, True
                    )
                ],
            )
        compare(local.rev_parse('deploy', short=False), expected=second)
        log.check(
            (
                'chimera-up-to-date: refs',
                {'git': {'before': {'deploy': first}, 'after': {'deploy': second}}},
            )
        )

    def test_deploy_checked_out_elsewhere_left_in_place(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        origin, local = _chimera_clone(tmpdir, replace)
        first = origin.rev_parse('main', short=False)
        origin.commit_content('second')
        local('fetch', 'origin')
        local('checkout', 'origin/main', '-B', 'main')
        local('branch', 'deploy', first)
        local('worktree', 'add', str(tmpdir / 'deploy-wt'), 'deploy')
        compare(
            _run(ChimeraUpToDateCheck(), _ws(tmpdir), fix=True),
            expected=[
                Finding(
                    'chimera-up-to-date',
                    f'{local.path} deploy does not point at main — '
                    'could not repoint, branch checked out elsewhere',
                    False,
                    True,
                )
            ],
        )
        compare(local.rev_parse('deploy', short=False), expected=first)  # left in place


def _shell_home(tmpdir: TempDir, replace: Replacer, shell: str):
    home = tmpdir.makedir('home')
    replace.in_environ('HOME', str(home))
    replace.in_environ('SHELL', f'/bin/{shell}')
    return home


class TestShellCompletion:
    def test_unknown_shell_is_silent(self, tmpdir: TempDir, replace: Replacer) -> None:
        _shell_home(tmpdir, replace, 'fish')
        compare(_run(ShellCompletionCheck(), _ws(tmpdir)), expected=[])

    def test_no_shell_is_silent(self, tmpdir: TempDir) -> None:
        # conftest clears $SHELL — nothing to verify
        compare(_run(ShellCompletionCheck(), _ws(tmpdir)), expected=[])

    def test_zsh_installed_script_is_silent(self, tmpdir: TempDir, replace: Replacer) -> None:
        home = _shell_home(tmpdir, replace, 'zsh')
        (home / '.zfunc').mkdir()
        (home / '.zfunc' / '_ch').write_text('#compdef ch')
        compare(_run(ShellCompletionCheck(), _ws(tmpdir)), expected=[])

    def test_zsh_eval_line_is_silent(self, tmpdir: TempDir, replace: Replacer) -> None:
        home = _shell_home(tmpdir, replace, 'zsh')
        (home / '.zshrc').write_text('eval "$(env _CH_COMPLETE=source_zsh ch)"\n')
        compare(_run(ShellCompletionCheck(), _ws(tmpdir)), expected=[])

    def test_zsh_missing_reported(self, tmpdir: TempDir, replace: Replacer) -> None:
        home = _shell_home(tmpdir, replace, 'zsh')
        (home / '.zshrc').write_text('# no completion here\n')
        compare(
            _run(ShellCompletionCheck(), _ws(tmpdir)),
            expected=[
                Finding(
                    'shell-completion',
                    'tab completion for ch is not installed for zsh — '
                    'run `ch --install-completion`, or add to ~/.zshrc:\n'
                    '    eval "$(env _CH_COMPLETE=source_zsh ch)"',
                    False,
                    False,
                )
            ],
        )

    def test_bash_installed_script_is_silent(self, tmpdir: TempDir, replace: Replacer) -> None:
        home = _shell_home(tmpdir, replace, 'bash')
        (home / '.bash_completions').mkdir()
        (home / '.bash_completions' / 'ch.sh').write_text('complete')
        compare(_run(ShellCompletionCheck(), _ws(tmpdir)), expected=[])

    def test_bash_eval_line_in_profile_is_silent(self, tmpdir: TempDir, replace: Replacer) -> None:
        home = _shell_home(tmpdir, replace, 'bash')
        (home / '.bash_profile').write_text('eval "$(env _CH_COMPLETE=source_bash ch)"\n')
        compare(_run(ShellCompletionCheck(), _ws(tmpdir)), expected=[])

    def test_bash_missing_reported(self, tmpdir: TempDir, replace: Replacer) -> None:
        _shell_home(tmpdir, replace, 'bash')
        compare(
            _run(ShellCompletionCheck(), _ws(tmpdir)),
            expected=[
                Finding(
                    'shell-completion',
                    'tab completion for ch is not installed for bash — '
                    'run `ch --install-completion`, or add to ~/.bashrc:\n'
                    '    eval "$(env _CH_COMPLETE=source_bash ch)"',
                    False,
                    False,
                )
            ],
        )
