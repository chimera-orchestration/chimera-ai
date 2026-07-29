import subprocess

from testfixtures import ShouldRaise, compare


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
