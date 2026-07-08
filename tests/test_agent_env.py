from testfixtures import Replacer, ShouldRaise, compare, not_there

from chimera.agent_env import (
    CrossScopeError,
    fenced_project,
    refuse_cross_scope,
    role_env,
    role_scope,
    running_under_ai_agent,
    session_role,
)


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


class TestFencedProject:
    def test_scoped_manager_is_fenced(self, replace: Replacer) -> None:
        replace.in_environ('CHIMERA_ROLE', 'manager')
        replace.in_environ('CHIMERA_ROLE_SCOPE', 'proj')
        compare(fenced_project(), expected='proj')

    def test_scoped_agent_is_fenced_to_its_project(self, replace: Replacer) -> None:
        replace.in_environ('CHIMERA_ROLE', 'agent')
        replace.in_environ('CHIMERA_ROLE_SCOPE', 'proj@g')
        compare(fenced_project(), expected='proj')

    def test_captain_is_unfenced(self, replace: Replacer) -> None:
        replace.in_environ('CHIMERA_ROLE', 'captain')
        replace.in_environ('CHIMERA_ROLE_SCOPE', 'proj')  # even a stray scope never fences
        assert fenced_project() is None

    def test_no_role_is_unfenced(self, replace: Replacer) -> None:
        replace.in_environ('CHIMERA_ROLE_SCOPE', 'proj')
        assert fenced_project() is None  # conftest clears the role variable

    def test_role_without_scope_is_unfenced(self, replace: Replacer) -> None:
        replace.in_environ('CHIMERA_ROLE', 'manager')
        assert fenced_project() is None


class TestRefuseCrossScope:
    def test_out_of_scope_refuses(self, replace: Replacer) -> None:
        replace.in_environ('CHIMERA_ROLE', 'manager')
        replace.in_environ('CHIMERA_ROLE_SCOPE', 'proj')
        with ShouldRaise(CrossScopeError('proj')):
            refuse_cross_scope('other')

    def test_in_scope_passes(self, replace: Replacer) -> None:
        replace.in_environ('CHIMERA_ROLE', 'manager')
        replace.in_environ('CHIMERA_ROLE_SCOPE', 'proj')
        refuse_cross_scope('proj')

    def test_unfenced_passes_anything(self) -> None:
        refuse_cross_scope('other')  # conftest clears role and scope

    def test_error_signposts_depth_never_privilege(self) -> None:
        compare(str(CrossScopeError('proj')), expected='scoped to proj; ask the captain')


class TestRoleEnv:
    def test_unscoped(self) -> None:
        compare(role_env('captain'), expected={'CHIMERA_ROLE': 'captain'})

    def test_scoped(self) -> None:
        compare(
            role_env('agent', 'proj@g'),
            expected={'CHIMERA_ROLE': 'agent', 'CHIMERA_ROLE_SCOPE': 'proj@g'},
        )
