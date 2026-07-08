from testfixtures import Replacer, compare, not_there

from chimera.agent_env import role_scope, running_under_ai_agent, session_role


class TestRunningUnderAiAgent:
    def test_true_when_claudecode_set(self, replace: Replacer) -> None:
        replace.in_environ('CLAUDECODE', '1')
        assert running_under_ai_agent()

    def test_false_when_unset(self, replace: Replacer) -> None:
        replace.in_environ('CLAUDECODE', not_there)
        assert not running_under_ai_agent()


class TestSessionRole:
    def test_reads_the_role(self, replace: Replacer) -> None:
        replace.in_environ('CHIMERA_ROLE', 'manager')
        compare(session_role(), expected='manager')

    def test_unset_is_none(self) -> None:
        assert session_role() is None  # conftest clears the variable

    def test_empty_counts_as_unset(self, replace: Replacer) -> None:
        replace.in_environ('CHIMERA_ROLE', '')
        assert session_role() is None


class TestRoleScope:
    def test_reads_the_scope(self, replace: Replacer) -> None:
        replace.in_environ('CHIMERA_ROLE_SCOPE', 'proj@g')
        compare(role_scope(), expected='proj@g')

    def test_unset_is_none(self) -> None:
        assert role_scope() is None

    def test_empty_counts_as_unset(self, replace: Replacer) -> None:
        replace.in_environ('CHIMERA_ROLE_SCOPE', '')
        assert role_scope() is None
