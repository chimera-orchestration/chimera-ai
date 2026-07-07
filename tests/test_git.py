import pytest
from giterator import GitError
from giterator.testing import Repo
from testfixtures import LogCapture, Replacer, ShouldRaise, TempDir, compare
from testfixtures.loguru import LoguruSource

from chimera.git import HTTP_TIMEOUTS, SSH_COMMAND, Git, _env


def _trace() -> LogCapture:
    """Capture everything, DEBUG included — the command trace is what these tests cover."""
    return LogCapture(LoguruSource(('message', 'extra')))


def _refs() -> LogCapture:
    """INFO+ only: the ref_log lines, without the DEBUG trace of the commands that made them."""
    return LogCapture(LoguruSource(('message', 'extra'), level='INFO'))


class TestTrace:
    def test_command_logs_with_its_cwd(self, git_repo: Repo) -> None:
        with _trace() as log:
            Git(git_repo.path)('rev-parse', '--git-dir')
        log.check(('git rev-parse --git-dir', {'git_cwd': str(git_repo.path)}))

    def test_arguments_are_shell_quoted(self, git_repo: Repo) -> None:
        with _trace() as log:
            Git(git_repo.path)('log', '-1', '--format=%s and spaces')
        log.check(("git log -1 '--format=%s and spaces'", {'git_cwd': str(git_repo.path)}))

    def test_cwd_override_is_what_gets_traced(self, git_repo: Repo, tmpdir: TempDir) -> None:
        with _trace() as log:
            Git(tmpdir / 'nowhere')('rev-parse', '--git-dir', cwd=git_repo.path)
        log.check(('git rev-parse --git-dir', {'git_cwd': str(git_repo.path)}))

    def test_a_failing_command_was_already_traced(self, git_repo: Repo) -> None:
        # the line lands before the subprocess runs, so a hung or crashed command is visible
        with _trace() as log:
            with ShouldRaise(GitError, match='no-such-subcommand'):
                Git(git_repo.path)('no-such-subcommand')
        log.check(('git no-such-subcommand', {'git_cwd': str(git_repo.path)}))

    @pytest.mark.parametrize('var', ['_CH_COMPLETE', '_CHIMERA_COMPLETE'])
    def test_shell_completion_suppresses_the_trace(
        self, git_repo: Repo, replace: Replacer, var: str
    ) -> None:
        replace.in_environ(var, 'zsh_complete')
        with _trace() as log:
            Git(git_repo.path)('rev-parse', '--git-dir')
        log.check()


class TestEnv:
    def test_injects_timeouts_when_unset(self) -> None:
        compare(_env({}, None), expected={'GIT_SSH_COMMAND': SSH_COMMAND, **HTTP_TIMEOUTS})

    def test_user_ssh_command_wins(self) -> None:
        compare(
            _env({'GIT_SSH_COMMAND': 'ssh -F mine'}, None),
            expected={'GIT_SSH_COMMAND': 'ssh -F mine', **HTTP_TIMEOUTS},
        )

    def test_user_git_ssh_wins(self) -> None:
        compare(
            _env({'GIT_SSH': '/usr/bin/ssh'}, None),
            expected={'GIT_SSH': '/usr/bin/ssh', **HTTP_TIMEOUTS},
        )

    def test_user_http_tuning_wins(self) -> None:
        compare(
            _env({'GIT_HTTP_LOW_SPEED_LIMIT': '1'}, None)['GIT_HTTP_LOW_SPEED_LIMIT'],
            expected='1',
        )

    def test_caller_env_merges_last(self) -> None:
        compare(
            _env({'A': 'base'}, {'A': 'caller', 'B': 'new'}),
            expected={
                'A': 'caller',
                'B': 'new',
                'GIT_SSH_COMMAND': SSH_COMMAND,
                **HTTP_TIMEOUTS,
            },
        )

    def test_subprocess_sees_environ_then_caller_overrides(
        self, git_repo: Repo, replace: Replacer
    ) -> None:
        # GIT_CONFIG_* is read by git itself, so it proves what env the subprocess really got:
        # os.environ flows through the merge, and a caller ``env=`` outranks it.
        replace.in_environ('GIT_CONFIG_COUNT', '1')
        replace.in_environ('GIT_CONFIG_KEY_0', 'chimera.marker')
        replace.in_environ('GIT_CONFIG_VALUE_0', 'from-environ')
        git = Git(git_repo.path)
        compare(git('config', 'chimera.marker').strip(), expected='from-environ')
        compare(
            git('config', 'chimera.marker', env={'GIT_CONFIG_VALUE_0': 'from-caller'}).strip(),
            expected='from-caller',
        )


class TestRefHelpers:
    def test_ref_exists(self, git_repo: Repo) -> None:
        git = Git(git_repo.path)
        assert git.ref_exists('HEAD')
        assert not git.ref_exists('no-such-ref')

    def test_ref_shas_maps_only_existing_refs_to_full_shas(self, git_repo: Repo) -> None:
        git = Git(git_repo.path)
        sha = git.rev_parse('HEAD', short=False)
        compare(git.ref_shas('HEAD', 'no-such-ref'), expected={'HEAD': sha})


class TestRefLog:
    def test_a_mutation_logs_before_and_after(self, git_repo: Repo) -> None:
        git = Git(git_repo.path)
        sha = git.rev_parse('HEAD', short=False)
        with _refs() as log:
            with git.ref_log('demo: refs', 'twig'):
                git('branch', 'twig')
        log.check(('demo: refs', {'git': {'before': {}, 'after': {'twig': sha}}}))

    def test_no_change_is_silent(self, git_repo: Repo) -> None:
        with _refs() as log:
            with Git(git_repo.path).ref_log('demo: refs', 'HEAD'):
                pass
        log.check()

    def test_always_logs_even_unchanged(self, git_repo: Repo) -> None:
        git = Git(git_repo.path)
        sha = git.rev_parse('HEAD', short=False)
        with _refs() as log:
            with git.ref_log('demo: refs', 'HEAD', always=True):
                pass
        log.check(('demo: refs', {'git': {'before': {'HEAD': sha}, 'after': {'HEAD': sha}}}))

    def test_bind_keys_ride_the_line(self, git_repo: Repo) -> None:
        git = Git(git_repo.path)
        sha = git.rev_parse('HEAD', short=False)
        with _refs() as log:
            with git.ref_log('demo: refs', 'twig', goal='g') as reflog:
                git('branch', 'twig')
                reflog.bind(worktree='/somewhere')
        log.check(
            (
                'demo: refs',
                {
                    'git': {'before': {}, 'after': {'twig': sha}},
                    'goal': 'g',
                    'worktree': '/somewhere',
                },
            )
        )

    def test_an_exception_still_records_completed_mutations(self, git_repo: Repo) -> None:
        # the log must be enough to undo whatever the block managed to change before it died
        git = Git(git_repo.path)
        sha = git.rev_parse('HEAD', short=False)
        with _refs() as log:
            with ShouldRaise(RuntimeError('boom')):
                with git.ref_log('demo: refs', 'twig'):
                    git('branch', 'twig')
                    raise RuntimeError('boom')
        log.check(('demo: refs', {'git': {'before': {}, 'after': {'twig': sha}}}))
