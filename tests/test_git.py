from giterator import GitError
from giterator.testing import Repo
from testfixtures import LogCapture, Replacer, ShouldRaise, TempDir, compare
from testfixtures.loguru import LoguruSource

from chimera.git import (
    HTTP_TIMEOUTS,
    SSH_COMMAND,
    Git,
    _env,
    remote_repo,
    remote_slug,
    sibling_url,
)


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

    def test_traces_even_while_completing(self, git_repo: Repo, replace: Replacer) -> None:
        # muting is the sink's job (``main`` drops them while completing), never this end's
        replace.in_environ('_CH_COMPLETE', 'zsh_complete')
        with _trace() as log:
            Git(git_repo.path)('rev-parse', '--git-dir')
        log.check(('git rev-parse --git-dir', {'git_cwd': str(git_repo.path)}))


class TestRaw:
    def test_returns_output_undecoded(self, git_repo: Repo) -> None:
        (git_repo.path / 'legacy.csv').write_bytes(b'M\xf6tley Cr\xfce\n')  # not valid UTF-8
        git_repo('add', 'legacy.csv')
        git_repo('commit', '-qm', 'latin-1 export')
        compare(
            Git(git_repo.path).raw('show', 'HEAD:legacy.csv'),
            expected=b'M\xf6tley Cr\xfce\n',
        )

    def test_traces_like_call(self, git_repo: Repo) -> None:
        with _trace() as log:
            Git(git_repo.path).raw('rev-parse', '--git-dir')
        log.check(('git rev-parse --git-dir', {'git_cwd': str(git_repo.path)}))

    def test_failure_raises_git_error(self, git_repo: Repo) -> None:
        with ShouldRaise(GitError, match='no-such-subcommand'):
            Git(git_repo.path).raw('no-such-subcommand')


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
        log.check_empty()

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


def test_remote_slug_shapes() -> None:
    compare(remote_slug('git@github.com:Owner/Repo.git'), expected='owner/repo')
    compare(remote_slug('https://github.com/Owner/Repo'), expected='owner/repo')
    compare(remote_slug('ssh://git@github.com/Owner/Repo.git'), expected='owner/repo')
    compare(remote_slug('file:///Users/me/repos/fork.git'), expected='')  # a path, not an owner
    compare(remote_slug('/Users/me/repos/fork'), expected='')


def test_sibling_url_shapes() -> None:
    compare(
        sibling_url('git@github.com:Owner/Repo.git', 'alice/fork'),
        expected='git@github.com:alice/fork.git',
    )
    compare(
        sibling_url('https://github.com/Owner/Repo', 'alice/fork'),
        expected='https://github.com/alice/fork',
    )
    compare(
        sibling_url('ssh://git@ghe.corp.example/Team/Proj.git', 'alice/fork'),
        expected='ssh://git@ghe.corp.example/alice/fork.git',
    )
    compare(sibling_url('/Users/me/repos/fork', 'alice/fork'), expected='')  # no host to keep
    compare(sibling_url('file:///Users/me/repos/fork.git', 'alice/fork'), expected='')


def test_remote_repo_shapes() -> None:
    compare(remote_repo('git@github.com:Owner/Repo.git'), expected='github.com/owner/repo')
    compare(
        remote_repo('https://ghe.corp.example/Team/Proj.git'),
        expected='ghe.corp.example/team/proj',
    )
    compare(remote_repo('/Users/me/repos/fork'), expected='')
    compare(remote_repo(':Owner/Repo'), expected='')  # slug but no host: nothing to pin
