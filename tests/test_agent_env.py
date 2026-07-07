from testfixtures import Replacer, compare, not_there

from chimera.agent_env import running_under_ai_agent


class TestRunningUnderAiAgent:
    def test_true_when_claudecode_set(self, replace: Replacer) -> None:
        replace.in_environ('CLAUDECODE', '1')
        compare(running_under_ai_agent(), expected=True)

    def test_false_when_unset(self, replace: Replacer) -> None:
        replace.in_environ('CLAUDECODE', not_there)
        compare(running_under_ai_agent(), expected=False)
