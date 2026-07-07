from testfixtures import Replacer, not_there

from chimera.agent_env import running_under_ai_agent


class TestRunningUnderAiAgent:
    def test_true_when_claudecode_set(self, replace: Replacer) -> None:
        replace.in_environ('CLAUDECODE', '1')
        assert running_under_ai_agent()

    def test_false_when_unset(self, replace: Replacer) -> None:
        replace.in_environ('CLAUDECODE', not_there)
        assert not running_under_ai_agent()
