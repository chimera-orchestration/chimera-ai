import shutil
import subprocess

import yaml
from giterator import Git
from giterator.testing import Repo
from testfixtures import LogCapture, Replacer, TempDir, compare, not_there
from testfixtures.loguru import LoguruSource

from chimera.commands.doctor import checks as doctor_checks
from chimera.commands.doctor.checks import (
    ChimeraUpToDateCheck,
    GitignoreCheck,
    InertBranchCheck,
    LegacyWorktreeSeparatorCheck,
    OrphanedWorktreeCheck,
    ProjectConfigCheck,
    ShellCompletionCheck,
    StaleHumanWorktreeCheck,
    WorkspaceCommitCheck,
    WorkspaceConfigCheck,
    WorkspaceEnvCheck,
    WorktreeBranchCheck,
    commit_message,
)
from chimera.commands.doctor.core import Check, Exclusions, Finding
from chimera.commands.init import TEMPLATE
from chimera.worktrees import is_dirty, registered_worktrees


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


def _run(check: Check, ws, fix: bool = False, exclude: Exclusions | None = None) -> list[Finding]:
    return list(check.run(ws, fix, exclude if exclude is not None else Exclusions()))


def _config(path):
    return yaml.safe_load((path / 'config.yaml').read_text())


def _env_finding(ws, where: str) -> Finding:
    """The workspace-env finding text — where: the '… is not set'/'… not this workspace' clause."""
    return Finding(
        'workspace-env',
        f'$CHIMERA_WORKSPACE {where} — '
        'add to your shell profile (~/.zshrc, ~/.bashrc, ~/.profile):\n'
        f'    export CHIMERA_WORKSPACE="{ws}"',
        resolved=False,
        fixable=False,
    )


class TestWorkspaceConfig:
    def test_missing_reports_without_writing(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        compare(
            _run(WorkspaceConfigCheck(), ws),
            expected=[
                Finding(
                    'workspace-config', f'{ws}/config.yaml missing', resolved=False, fixable=True
                )
            ],
        )
        assert (ws / 'config.yaml').exists() is False  # report only, nothing written

    def test_missing_fixed(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        compare(
            _run(WorkspaceConfigCheck(), ws, fix=True),
            expected=[
                Finding(
                    'workspace-config', f'{ws}/config.yaml missing', resolved=True, fixable=True
                )
            ],
        )
        compare(_config(ws), expected={'kind': 'workspace'})

    def test_legacy_keeps_other_keys(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        tmpdir.dump('lycia/config.yaml', {'name': 'lycia'})
        compare(
            _run(WorkspaceConfigCheck(), ws, fix=True),
            expected=[
                Finding(
                    'workspace-config',
                    f'{ws}/config.yaml missing kind: workspace',
                    resolved=True,
                    fixable=True,
                )
            ],
        )
        compare(_config(ws), expected={'kind': 'workspace', 'name': 'lycia'})

    def test_excluded_fix_reports_without_writing(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        compare(
            _run(WorkspaceConfigCheck(), ws, fix=True, exclude=Exclusions(('workspace-config',))),
            expected=[
                Finding(
                    'workspace-config', f'{ws}/config.yaml missing', resolved=False, fixable=True
                )
            ],
        )
        assert (ws / 'config.yaml').exists() is False  # the excluded fix never ran

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
                    resolved=False,
                    fixable=False,
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
                    resolved=False,
                    fixable=False,
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
            expected=[
                Finding(
                    'gitignore',
                    f"{ws / '.gitignore'} missing 'logs/'",
                    resolved=False,
                    fixable=True,
                )
            ],
        )

    def test_excluded_entry_not_written(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        gitignore = ws / '.gitignore'
        gitignore.write_text('*.lock\nservices-running.jsonl\n*/worktrees/\n')
        compare(
            _run(GitignoreCheck(), ws, fix=True, exclude=Exclusions(('*/repo/',))),
            expected=[
                Finding('gitignore', f"{gitignore} missing 'logs/'", resolved=True, fixable=True),
                Finding(
                    'gitignore', f"{gitignore} missing '*/repo/'", resolved=False, fixable=True
                ),
            ],
        )
        compare(  # the excluded entry stays missing; the other was appended
            gitignore.read_text(),
            expected='*.lock\nservices-running.jsonl\n*/worktrees/\nlogs/\n',
        )

    def test_missing_entry_appended_after_an_unterminated_final_line(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        (ws / '.gitignore').write_text('*.lock\nservices-running.jsonl\n*/repo/\n*/worktrees/')
        compare(
            _run(GitignoreCheck(), ws, fix=True),
            expected=[
                Finding(
                    'gitignore', f"{ws / '.gitignore'} missing 'logs/'", resolved=True, fixable=True
                )
            ],
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
                Finding(
                    'gitignore',
                    f'{ws / ".gitignore"} missing {entry!r}',
                    resolved=True,
                    fixable=True,
                )
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
                    'project-config',
                    f'{project}/config.yaml missing kind: project',
                    resolved=True,
                    fixable=True,
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
                    resolved=True,
                    fixable=True,
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
                    resolved=False,
                    fixable=False,
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
                    'project-config',
                    f'{project}/config.yaml has no kind and no repo',
                    resolved=False,
                    fixable=False,
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
            expected=[
                Finding(
                    'human-worktrees',
                    f'stale human worktree {worktree}',
                    resolved=True,
                    fixable=True,
                )
            ],
        )
        tmpdir.compare(path='lycia/proj/worktrees', expected=())
        compare(Git(git_repo.path).branches(), expected=['g1/human', 'main'])

    def test_clean_report_only_leaves_it(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = _human_worktree(git_repo, project, 'g1')
        compare(
            _run(StaleHumanWorktreeCheck(), ws),
            expected=[
                Finding(
                    'human-worktrees',
                    f'stale human worktree {worktree}',
                    resolved=False,
                    fixable=True,
                )
            ],
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
                    resolved=False,
                    fixable=False,
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
                    resolved=False,
                    fixable=False,
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
                    resolved=True,
                    fixable=True,
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
                    resolved=False,
                    fixable=True,
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
                Finding(
                    'worktree-separator',
                    'legacy worktree g-reviewer → g@reviewer',
                    resolved=True,
                    fixable=True,
                )
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

    def test_ignores_nested_prefix_branch(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = project / 'worktrees' / 'old-goal@A'
        Git(git_repo.path)('worktree', 'add', '-b', 'parked/new-goal/A', str(worktree), 'main')
        compare(_run(LegacyWorktreeSeparatorCheck(), ws, fix=True), expected=[])
        tmpdir.compare(['old-goal@A'], path='lycia/proj/worktrees', recursive=False)


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
                        'worktree-branch',
                        f'{worktree} is on stray, expected g/agent',
                        resolved=True,
                        fixable=True,
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
                Finding(
                    'worktree-branch',
                    f'{worktree} is on stray, expected g/agent',
                    resolved=False,
                    fixable=True,
                )
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
                    resolved=False,
                    fixable=False,
                )
            ],
        )
        compare(Git(worktree)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='stray')

    def test_leftover_worktree_removed(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = _agent_worktree(git_repo, project, 'g')
        _flip(worktree, to='parked/g/agent')
        git = Git(git_repo.path)
        git('branch', '-D', 'g/agent')  # the goal is finished; the work lives on parked/…
        sha = git.rev_parse('parked/g/agent', short=False)
        with LogCapture(LoguruSource(('message', 'extra'))) as log:
            compare(
                _run(WorktreeBranchCheck(), ws, fix=True),
                expected=[
                    Finding(
                        'worktree-branch',
                        f'{worktree} is on parked/g/agent, but branch g/agent is gone '
                        '— leftover worktree',
                        resolved=True,
                        fixable=True,
                    )
                ],
            )
        tmpdir.compare(path='lycia/proj/worktrees', expected=())
        compare('parked/g/agent' in git.branches(), expected=True)  # the work survives
        log.check(
            (
                'worktree-branch: removed',
                {'worktree': str(worktree), 'branch': 'parked/g/agent', 'sha': sha},
            )
        )

    def test_leftover_worktree_report_only(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = _agent_worktree(git_repo, project, 'g')
        _flip(worktree)
        Git(git_repo.path)('branch', '-D', 'g/agent')
        compare(
            _run(WorktreeBranchCheck(), ws),
            expected=[
                Finding(
                    'worktree-branch',
                    f'{worktree} is on stray, but branch g/agent is gone — leftover worktree',
                    resolved=False,
                    fixable=True,
                )
            ],
        )
        tmpdir.compare(['g@agent'], path='lycia/proj/worktrees', recursive=False)  # left as-is

    def test_leftover_dirty_left_in_place(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = _agent_worktree(git_repo, project, 'g')
        _flip(worktree)
        Git(git_repo.path)('branch', '-D', 'g/agent')
        (worktree / 'scratch.txt').write_text('wip')
        compare(
            _run(WorktreeBranchCheck(), ws, fix=True),
            expected=[
                Finding(
                    'worktree-branch',
                    f'{worktree} is on stray, but branch g/agent is gone '
                    '— uncommitted changes, left in place',
                    resolved=False,
                    fixable=False,
                )
            ],
        )
        compare((worktree / 'scratch.txt').read_text(), expected='wip')

    def test_leftover_detached_left_in_place(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = _agent_worktree(git_repo, project, 'g')
        Git(worktree)('checkout', '--detach')
        Git(git_repo.path)('branch', '-D', 'g/agent')  # HEAD may be the commits' only anchor
        compare(
            _run(WorktreeBranchCheck(), ws, fix=True),
            expected=[
                Finding(
                    'worktree-branch',
                    f'{worktree} is on HEAD, but branch g/agent is gone — detached, left in place',
                    resolved=False,
                    fixable=False,
                )
            ],
        )
        tmpdir.compare(['g@agent'], path='lycia/proj/worktrees', recursive=False)

    def test_detached_head_fixed(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = _agent_worktree(git_repo, project, 'g')
        Git(worktree)('checkout', '--detach')
        compare(
            _run(WorktreeBranchCheck(), ws, fix=True),
            expected=[
                Finding(
                    'worktree-branch',
                    f'{worktree} is on HEAD, expected g/agent',
                    resolved=True,
                    fixable=True,
                )
            ],
        )
        compare(Git(worktree)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='g/agent')

    def test_excluded_worktree_left_alone(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        worktree = _agent_worktree(git_repo, project, 'g')
        _flip(worktree)
        compare(
            _run(WorktreeBranchCheck(), ws, fix=True, exclude=Exclusions((str(worktree),))),
            expected=[
                Finding(
                    'worktree-branch',
                    f'{worktree} is on stray, expected g/agent',
                    resolved=False,
                    fixable=True,
                )
            ],
        )
        compare(Git(worktree)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='stray')

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


def _cloned_project(tmpdir, ws):
    """A project whose repo is a clone of an origin (so `main` is pushed)."""
    origin = Repo.make(tmpdir / 'origin')
    origin.commit_content('seed')
    local = Repo.clone(origin.path, tmpdir / 'repo')
    return local, _project(tmpdir, ws, local.path)


def _bare_branch(repo, ref, base='main'):
    Git(repo.path)('branch', '--no-track', ref, base)


class TestInertBranch:
    def test_pushed_branch_deleted(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo, project = _cloned_project(tmpdir, ws)
        _agent_worktree(repo, project, 'g')  # makes 'g' a known goal
        _bare_branch(repo, 'g/human')  # sits at main, which is on origin/main
        seed = Git(repo.path).rev_parse('g/human', short=False)
        with LogCapture(LoguruSource(('message', 'extra'))) as log:
            compare(
                _run(InertBranchCheck(), ws, fix=True),
                expected=[
                    Finding(
                        'inert-branches',
                        'g/human points at an already-integrated commit — inert',
                        resolved=True,
                        fixable=True,
                    )
                ],
            )
        compare('g/human' in set(Git(repo.path).branches()), expected=False)
        log.check(('inert-branches: refs', {'git': {'before': {'g/human': seed}, 'after': {}}}))

    def test_report_only_leaves_it(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo, project = _cloned_project(tmpdir, ws)
        _agent_worktree(repo, project, 'g')
        _bare_branch(repo, 'g/human')
        compare(
            _run(InertBranchCheck(), ws),
            expected=[
                Finding(
                    'inert-branches',
                    'g/human points at an already-integrated commit — inert',
                    resolved=False,
                    fixable=True,
                )
            ],
        )
        compare('g/human' in set(Git(repo.path).branches()), expected=True)

    def test_excluded_branch_reported_but_kept(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo, project = _cloned_project(tmpdir, ws)
        _agent_worktree(repo, project, 'g')
        _bare_branch(repo, 'g/human')
        compare(
            _run(InertBranchCheck(), ws, fix=True, exclude=Exclusions(('g/human',))),
            expected=[
                Finding(
                    'inert-branches',
                    'g/human points at an already-integrated commit — inert',
                    resolved=False,
                    fixable=True,
                )
            ],
        )
        compare('g/human' in set(Git(repo.path).branches()), expected=True)

    def test_ancestor_of_local_default_deleted_without_a_remote(
        self, tmpdir: TempDir, git_repo: Repo
    ) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)  # no origin at all
        _agent_worktree(git_repo, project, 'g')
        _bare_branch(git_repo, 'g/human')  # == main tip → ancestor of local main
        compare(
            _run(InertBranchCheck(), ws, fix=True),
            expected=[
                Finding(
                    'inert-branches',
                    'g/human points at an already-integrated commit — inert',
                    resolved=True,
                    fixable=True,
                )
            ],
        )
        compare('g/human' in set(Git(git_repo.path).branches()), expected=False)

    def test_branch_with_unique_unpushed_work_kept(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        _agent_worktree(git_repo, project, 'g')
        temp = project / 'worktrees' / 'g@human'  # commit on human, then drop the worktree
        Git(git_repo.path)('worktree', 'add', '-b', 'g/human', str(temp), 'main')
        Repo(temp).commit_content('unpushed human work')
        Git(git_repo.path)('worktree', 'remove', str(temp))  # g/human now bare and ahead of main
        compare(_run(InertBranchCheck(), ws), expected=[])  # unique work, not integrated → kept

    def test_checked_out_branch_left_alone(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        _agent_worktree(git_repo, project, 'g')
        Git(git_repo.path)('worktree', 'add', '-b', 'g/human', str(tmpdir / 'human'), 'main')
        compare(_run(InertBranchCheck(), ws), expected=[])  # checked out → never force-deleted

    def test_agent_branch_never_flagged(self, tmpdir: TempDir, git_repo: Repo) -> None:
        ws = _ws(tmpdir)
        project = _project(tmpdir, ws, git_repo.path)
        _agent_worktree(git_repo, project, 'g')  # only g/agent, sitting at main
        compare(_run(InertBranchCheck(), ws), expected=[])

    def test_custom_actor_flagged(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        repo, project = _cloned_project(tmpdir, ws)
        _agent_worktree(repo, project, 'g')
        _bare_branch(repo, 'g/reviewer')  # not human-specific
        compare(
            _run(InertBranchCheck(), ws, fix=True),
            expected=[
                Finding(
                    'inert-branches',
                    'g/reviewer points at an already-integrated commit — inert',
                    resolved=True,
                    fixable=True,
                )
            ],
        )
        compare('g/reviewer' in set(Git(repo.path).branches()), expected=False)

    def test_ignores_a_branch_whose_goal_has_no_agent_worktree(
        self, tmpdir: TempDir, git_repo: Repo
    ) -> None:
        ws = _ws(tmpdir)
        _project(tmpdir, ws, git_repo.path)
        _bare_branch(git_repo, 'g/human')  # no g@agent worktree → 'g' isn't a known goal
        compare(_run(InertBranchCheck(), ws), expected=[])

    def test_skips_project_without_a_live_repo(self, tmpdir: TempDir) -> None:
        ws = _ws(tmpdir)
        _project(tmpdir, ws, ws / 'proj' / 'repo')  # repo path doesn't exist
        compare(_run(InertBranchCheck(), ws), expected=[])


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
                    resolved=True,
                    fixable=True,
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
                    resolved=False,
                    fixable=True,
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
                    'orphaned-worktrees',
                    f'{leftover} is not a registered worktree',
                    resolved=False,
                    fixable=False,
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
        compare(doctor_checks.chimera_repo(nested / 'checks.py'), expected=repo.resolve())

    def test_none_without_a_checkout(self, tmpdir: TempDir) -> None:
        nested = tmpdir.makedir('a/b/c')
        compare(doctor_checks.chimera_repo(nested / 'checks.py'), expected=None)


def _chimera_clone(tmpdir: TempDir, replace: Replacer) -> tuple[Repo, Git]:
    """An origin repo and a clone wired together like a real dev checkout, patched in."""
    origin = Repo.make(tmpdir / 'origin')
    origin.commit_content('seed')
    local = Git.clone(origin, tmpdir / 'local')
    replace.in_module(doctor_checks.chimera_repo, lambda: local.path)
    return origin, local


class TestChimeraUpToDate:
    def test_no_checkout_is_silent(self, tmpdir: TempDir, replace: Replacer) -> None:
        replace.in_module(doctor_checks.chimera_repo, lambda: None)
        compare(_run(ChimeraUpToDateCheck(), _ws(tmpdir)), expected=[])

    def test_no_remote_is_silent(self, tmpdir: TempDir, replace: Replacer, git_repo: Repo) -> None:
        replace.in_module(doctor_checks.chimera_repo, lambda: git_repo.path)  # no origin at all
        compare(_run(ChimeraUpToDateCheck(), _ws(tmpdir)), expected=[])

    def test_logs_the_checkout_location(self, tmpdir: TempDir, replace: Replacer) -> None:
        _origin, local = _chimera_clone(tmpdir, replace)
        with LogCapture(LoguruSource(('message', 'extra'))) as log:
            _run(ChimeraUpToDateCheck(), _ws(tmpdir))
        log.check(('chimera-up-to-date: checkout', {'repo': str(local.path)}))

    def test_logs_no_checkout_found(self, tmpdir: TempDir, replace: Replacer) -> None:
        replace.in_module(doctor_checks.chimera_repo, lambda: None)
        with LogCapture(LoguruSource(('message', 'extra'))) as log:
            _run(ChimeraUpToDateCheck(), _ws(tmpdir))
        log.check(('chimera-up-to-date: checkout', {'repo': None}))

    def test_up_to_date_no_deploy_is_silent(self, tmpdir: TempDir, replace: Replacer) -> None:
        _chimera_clone(tmpdir, replace)
        compare(_run(ChimeraUpToDateCheck(), _ws(tmpdir)), expected=[])

    def test_deploy_matching_main_is_silent(self, tmpdir: TempDir, replace: Replacer) -> None:
        _origin, local = _chimera_clone(tmpdir, replace)
        local('branch', 'deploy', 'main')
        compare(_run(ChimeraUpToDateCheck(), _ws(tmpdir)), expected=[])

    def test_main_ahead_of_origin_is_silent(self, tmpdir: TempDir, replace: Replacer) -> None:
        _origin, local = _chimera_clone(tmpdir, replace)
        local('commit', '--allow-empty', '-m', 'unpushed')  # local has work origin lacks
        # origin is an ancestor of local — nothing to catch up, and not a divergence
        compare(_run(ChimeraUpToDateCheck(), _ws(tmpdir), fix=True), expected=[])

    def test_main_behind_origin_reported(self, tmpdir: TempDir, replace: Replacer) -> None:
        origin, local = _chimera_clone(tmpdir, replace)
        origin.commit_content('remote-ahead')  # local hasn't fetched this yet
        compare(
            _run(ChimeraUpToDateCheck(), _ws(tmpdir)),
            expected=[
                Finding(
                    'chimera-up-to-date',
                    f'{local.path} main is behind origin/main',
                    resolved=False,
                    fixable=True,
                )
            ],
        )
        # the check's own `git fetch` ran even without --fix — origin/main is now current
        compare(
            local.rev_parse('origin/main', short=False),
            expected=origin.rev_parse('main', short=False),
        )

    def test_main_behind_origin_fast_forwarded(self, tmpdir: TempDir, replace: Replacer) -> None:
        origin, local = _chimera_clone(tmpdir, replace)
        local('checkout', '-b', 'other')  # main isn't checked out here — branch -f can move it
        original = local.rev_parse('main', short=False)
        origin.commit_content('remote-ahead')
        with LogCapture(LoguruSource(('message', 'extra'))) as log:
            compare(
                _run(ChimeraUpToDateCheck(), _ws(tmpdir), fix=True),
                expected=[
                    Finding(
                        'chimera-up-to-date',
                        f'{local.path} main fast-forwarded to origin/main',
                        resolved=True,
                        fixable=True,
                    )
                ],
            )
        new_sha = origin.rev_parse('main', short=False)
        compare(local.rev_parse('main', short=False), expected=new_sha)
        log.check(
            ('chimera-up-to-date: checkout', {'repo': str(local.path)}),
            (
                'chimera-up-to-date: refs',
                {'git': {'before': {'main': original}, 'after': {'main': new_sha}}},
            ),
        )

    def test_main_behind_blocked_by_checkout_here(self, tmpdir: TempDir, replace: Replacer) -> None:
        origin, local = _chimera_clone(tmpdir, replace)
        original = local.rev_parse('main', short=False)
        origin.commit_content('remote-ahead')  # local stays on main — branch -f refuses
        compare(
            _run(ChimeraUpToDateCheck(), _ws(tmpdir), fix=True),
            expected=[
                Finding(
                    'chimera-up-to-date',
                    f'{local.path} main is behind origin/main — '
                    'could not fast-forward, branch checked out elsewhere',
                    resolved=False,
                    fixable=True,
                )
            ],
        )
        compare(local.rev_parse('main', short=False), expected=original)  # left in place

    def test_main_diverged_reported(self, tmpdir: TempDir, replace: Replacer) -> None:
        origin, local = _chimera_clone(tmpdir, replace)
        origin.commit_content('remote-only')
        local('commit', '--allow-empty', '-m', 'local-only')  # local and origin now diverge
        diverged = local.rev_parse('main', short=False)
        compare(
            _run(ChimeraUpToDateCheck(), _ws(tmpdir), fix=True),
            expected=[
                Finding(
                    'chimera-up-to-date',
                    f'{local.path} main has diverged from origin/main — needs a human to merge',
                    resolved=False,
                    fixable=False,
                )
            ],
        )
        compare(local.rev_parse('main', short=False), expected=diverged)  # untouched

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
                    'chimera-up-to-date',
                    f'{local.path} deploy does not point at main',
                    resolved=False,
                    fixable=True,
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
                        'chimera-up-to-date',
                        f'{local.path} deploy repointed to main',
                        resolved=True,
                        fixable=True,
                    )
                ],
            )
        compare(local.rev_parse('deploy', short=False), expected=second)
        log.check(
            ('chimera-up-to-date: checkout', {'repo': str(local.path)}),
            (
                'chimera-up-to-date: refs',
                {'git': {'before': {'deploy': first}, 'after': {'deploy': second}}},
            ),
        )

    def test_deploy_checked_out_clean_fast_forwarded(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        origin, local = _chimera_clone(tmpdir, replace)
        first = origin.rev_parse('main', short=False)
        origin.commit_content('second')
        local('fetch', 'origin')
        local('checkout', 'origin/main', '-B', 'main')
        local('branch', 'deploy', first)
        local('worktree', 'add', str(tmpdir / 'deploy-wt'), 'deploy')  # deploy is checked out
        second = local.rev_parse('main', short=False)
        with LogCapture(LoguruSource(('message', 'extra'))) as log:
            compare(
                _run(ChimeraUpToDateCheck(), _ws(tmpdir), fix=True),
                expected=[
                    Finding(
                        'chimera-up-to-date',
                        f'{local.path} deploy repointed to main',
                        resolved=True,
                        fixable=True,
                    )
                ],
            )
        compare(local.rev_parse('deploy', short=False), expected=second)  # ff'd in its checkout
        log.check(
            ('chimera-up-to-date: checkout', {'repo': str(local.path)}),
            (
                'chimera-up-to-date: refs',
                {'git': {'before': {'deploy': first}, 'after': {'deploy': second}}},
            ),
        )

    def test_deploy_checked_out_dirty_left_in_place(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        origin, local = _chimera_clone(tmpdir, replace)
        first = origin.rev_parse('main', short=False)
        origin.commit_content('second')
        local('fetch', 'origin')
        local('checkout', 'origin/main', '-B', 'main')
        local('branch', 'deploy', first)
        local('worktree', 'add', str(tmpdir / 'deploy-wt'), 'deploy')
        (tmpdir / 'deploy-wt' / 'scratch.txt').write_text('wip')  # checkout has uncommitted work
        compare(
            _run(ChimeraUpToDateCheck(), _ws(tmpdir), fix=True),
            expected=[
                Finding(
                    'chimera-up-to-date',
                    f'{local.path} deploy does not point at main — could not repoint; its '
                    'checkout has uncommitted changes or has diverged from main, needs a human',
                    resolved=False,
                    fixable=True,
                )
            ],
        )
        compare(local.rev_parse('deploy', short=False), expected=first)  # left in place

    def test_deploy_checked_out_diverged_left_in_place(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        origin, local = _chimera_clone(tmpdir, replace)
        first = origin.rev_parse('main', short=False)
        origin.commit_content('second')
        local('fetch', 'origin')
        local('checkout', 'origin/main', '-B', 'main')
        local('worktree', 'add', '-b', 'deploy', str(tmpdir / 'deploy-wt'), first)
        Repo(tmpdir / 'deploy-wt').commit_content('deploy-only')  # deploy now diverges from main
        diverged = Git(tmpdir / 'deploy-wt').rev_parse('deploy', short=False)
        compare(
            _run(ChimeraUpToDateCheck(), _ws(tmpdir), fix=True),
            expected=[
                Finding(
                    'chimera-up-to-date',
                    f'{local.path} deploy does not point at main — could not repoint; its '
                    'checkout has uncommitted changes or has diverged from main, needs a human',
                    resolved=False,
                    fixable=True,
                )
            ],
        )
        compare(local.rev_parse('deploy', short=False), expected=diverged)  # left in place

    def test_advance_checkout_none_when_branch_not_checked_out(self, tmpdir: TempDir) -> None:
        repo = Repo.make(tmpdir / 'r')
        repo.commit_content('seed')
        repo('branch', 'deploy')  # exists, but no worktree has it checked out
        compare(doctor_checks._advance_checkout(Git(repo.path), 'deploy', 'main'), expected=None)


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
                    resolved=False,
                    fixable=False,
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
                    resolved=False,
                    fixable=False,
                )
            ],
        )


def _git_ws(tmpdir: TempDir) -> Repo:
    """A healthy workspace that is also its own committed git repo (``.path`` is the root)."""
    repo = Repo.make(tmpdir / 'lycia')
    (repo.path / 'processes').mkdir()
    shutil.copy(TEMPLATE / '.gitignore', repo.path / '.gitignore')
    repo('add', '-A')
    repo('commit', '-m', 'seed')
    return repo


class TestWorkspaceClean:
    def test_not_a_git_repo_is_silent(self, tmpdir: TempDir) -> None:
        compare(_run(WorkspaceCommitCheck(), _ws(tmpdir)), expected=[])

    def test_clean_repo_is_silent(self, tmpdir: TempDir) -> None:
        compare(_run(WorkspaceCommitCheck(), _git_ws(tmpdir).path), expected=[])

    def test_dirty_reported_without_fix(self, tmpdir: TempDir) -> None:
        ws = _git_ws(tmpdir).path
        (ws / 'knowledge.md').write_text('a jotting')
        compare(
            _run(WorkspaceCommitCheck(), ws),
            expected=[
                Finding(
                    'workspace-clean',
                    f'{ws} has uncommitted changes',
                    resolved=False,
                    fixable=True,
                )
            ],
        )
        compare(is_dirty(ws), expected=True)  # reported only, nothing committed

    def test_fix_stages_commits_and_logs(self, tmpdir: TempDir, replace: Replacer) -> None:
        repo = _git_ws(tmpdir)
        ws = repo.path
        (ws / 'knowledge.md').write_text('a jotting')  # untracked content makes the ws dirty
        replace.in_module(doctor_checks.commit_message, lambda diff: 'Add a knowledge note')
        head = repo('rev-parse', '--abbrev-ref', 'HEAD').strip()
        before = repo.rev_parse(head, short=False)
        with LogCapture(LoguruSource(('message', 'extra'))) as log:
            compare(
                _run(WorkspaceCommitCheck(), ws, fix=True),
                expected=[
                    Finding(
                        'workspace-clean',
                        f'{ws} committed: Add a knowledge note',
                        resolved=True,
                        fixable=True,
                    )
                ],
            )
        compare(is_dirty(ws), expected=False)
        compare(repo('log', '-1', '--format=%s').strip(), expected='Add a knowledge note')
        log.check(
            (
                'workspace-clean: refs',
                {
                    'git': {
                        'before': {head: before},
                        'after': {head: repo.rev_parse(head, short=False)},
                    }
                },
            )
        )

    def test_fix_falls_back_to_the_real_model_when_unmocked(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        repo = _git_ws(tmpdir)
        (repo.path / 'knowledge.md').write_text('a jotting')
        replace.in_module(subprocess.run, _no_claude)  # claude absent → generic subject
        compare(
            _run(WorkspaceCommitCheck(), repo.path, fix=True),
            expected=[
                Finding(
                    'workspace-clean',
                    f'{repo.path} committed: Snapshot workspace changes',
                    resolved=True,
                    fixable=True,
                )
            ],
        )


def _no_claude(cmd, **kw):
    """A subprocess.run stub that errors on claude but runs every other command for real."""
    if cmd[0] == 'claude':
        raise FileNotFoundError('claude')
    return _real_run(cmd, **kw)


_real_run = subprocess.run


class TestCommitMessage:
    def test_uses_the_models_reply(self, replace: Replacer) -> None:
        seen: dict[str, object] = {}

        def fake_run(cmd, *, input, capture_output, text, check):
            seen.update(cmd=cmd, input=input, capture_output=capture_output, text=text, check=check)
            return subprocess.CompletedProcess(cmd, 0, stdout='Tidy the notes\n', stderr='')

        replace.in_module(subprocess.run, fake_run)
        compare(commit_message('a staged diff'), expected='Tidy the notes')
        compare(
            seen,
            expected={
                'cmd': ['claude', '-p', doctor_checks._COMMIT_PROMPT, '--model', 'haiku'],
                'input': 'a staged diff',
                'capture_output': True,
                'text': True,
                'check': True,
            },
        )

    def test_empty_reply_falls_back(self, replace: Replacer) -> None:
        replace.in_module(
            subprocess.run,
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout='  \n', stderr=''),
        )
        compare(commit_message('a staged diff'), expected='Snapshot workspace changes')

    def test_claude_missing_falls_back(self, replace: Replacer) -> None:
        replace.in_module(subprocess.run, _raise(FileNotFoundError('claude')))
        compare(commit_message('a staged diff'), expected='Snapshot workspace changes')

    def test_nonzero_exit_falls_back(self, replace: Replacer) -> None:
        replace.in_module(subprocess.run, _raise(subprocess.CalledProcessError(1, 'claude')))
        compare(commit_message('a staged diff'), expected='Snapshot workspace changes')


def _raise(error: Exception):
    def run(cmd, **kw):
        raise error

    return run
