import subprocess
from pathlib import Path

from testfixtures import ShouldRaise, TempDir, compare

from chimera.archive import Archive


class TestNoRealHarness:
    # the autouse guard in conftest: a test that reaches the real harness binary must fail
    # loudly rather than start a session. See agent-docs/unit-and-functional-testing.md.

    def test_refuses_a_harness_launch(self) -> None:
        with ShouldRaise(AssertionError) as raised:
            subprocess.Popen(['claude', '--name', 'proj@g@agent'])
        assert 'tried to run the real harness binary' in str(raised.raised)

    def test_refuses_a_harness_binary_named_by_path(self) -> None:
        with ShouldRaise(AssertionError):
            subprocess.Popen(['/usr/local/bin/claude', 'agents', '--json'])

    def test_lets_every_other_command_through(self) -> None:
        result = subprocess.run(['echo', 'hello'], capture_output=True, text=True)
        compare(result.stdout, expected='hello\n')

    def test_tolerates_a_string_command(self) -> None:
        # shell=True passes a str, not a sequence — the guard must not choke on it
        compare(subprocess.Popen('exit 0', shell=True).wait(), expected=0)


class TestNoLiveArchive:
    # the second containment: identity resolution reads the archive on every invocation,
    # and workspace resolution walks up from cwd — which, for the suite, is inside a
    # chimera worktree. See agent-docs/unit-and-functional-testing.md.

    def test_refuses_an_archive_outside_this_tests_directory(self, tmpdir: TempDir) -> None:
        with ShouldRaise(AssertionError) as raised:
            Archive.open(Path.home() / 'lycia' / 'state' / 'archive.db')
        assert 'outside its own directory' in str(raised.raised)

    def test_allows_this_tests_own(self, tmpdir: TempDir) -> None:
        Archive.open(tmpdir.path / 'state' / 'archive.db').close()
