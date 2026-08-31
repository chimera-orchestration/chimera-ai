from pathlib import Path

from testfixtures import ShouldRaise, compare

from chimera.addresses import Actor, Address, Captain, Manager
from chimera.config import ProjectConfig
from chimera.context import Project, Scope

WORKSPACE = Path('/ws')


def _project(name: str = 'chimera') -> Project:
    return Project(Path('/ws') / name, ProjectConfig(kind='project', repo=Path('/r')))


class TestRender:
    def test_captain(self) -> None:
        compare(Captain().render(), expected='@@captain')
        compare(str(Captain()), expected='@@captain')

    def test_manager(self) -> None:
        compare(Manager(project='chimera').render(), expected='chimera@@manager')

    def test_actor(self) -> None:
        compare(Actor('chimera', 'fix-tests', 'agent').render(), expected='chimera@fix-tests@agent')


class TestParse:
    def test_captain_round_trips(self) -> None:
        compare(Address.parse('@@captain'), expected=Captain())

    def test_manager_round_trips(self) -> None:
        compare(Address.parse('chimera@@manager'), expected=Manager(project='chimera'))

    def test_actor_round_trips(self) -> None:
        compare(
            Address.parse('chimera@fix-tests@agent'),
            expected=Actor('chimera', 'fix-tests', 'agent'),
        )

    def test_rejects_the_wrong_segment_count(self) -> None:
        for raw in ('manager', 'chimera@manager', 'a@b@c@d'):
            with ShouldRaise(
                ValueError(f"{raw!r} is not a valid address: expected exactly 2 '@'s")
            ):
                Address.parse(raw)

    def test_rejects_empty_project_and_goal_with_the_wrong_actor(self) -> None:
        with ShouldRaise(ValueError("'@@bob' is not a valid address")):
            Address.parse('@@bob')

    def test_rejects_empty_goal_with_the_wrong_actor(self) -> None:
        with ShouldRaise(ValueError("'chimera@@bob' is not a valid address")):
            Address.parse('chimera@@bob')

    def test_rejects_a_missing_project(self) -> None:
        with ShouldRaise(ValueError("'@fix-tests@agent' is not a valid address: missing project")):
            Address.parse('@fix-tests@agent')

    def test_rejects_a_reserved_actor_name(self) -> None:
        for actor in ('manager', 'captain'):
            with ShouldRaise(ValueError(f'{actor!r} is a reserved role name, not a valid actor')):
                Address.parse(f'chimera@fix-tests@{actor}')

    def test_rejects_a_missing_actor(self) -> None:
        # the actor arm is the only one whose last segment isn't a fixed literal, so it
        # was the only one nothing checked — `chimera@fix-tests@` parsed and round-tripped,
        # which is enough for `ch msg send` to mint a mailbox no session can ever read
        with ShouldRaise(ValueError('an actor address needs an actor')):
            Address.parse('chimera@fix-tests@')


class TestActorConstruction:
    def test_rejects_a_reserved_actor_name(self) -> None:
        for actor in ('manager', 'captain'):
            with ShouldRaise(ValueError(f'{actor!r} is a reserved role name, not a valid actor')):
                Actor('chimera', 'fix-tests', actor)

    def test_rejects_a_missing_actor(self) -> None:
        with ShouldRaise(ValueError('an actor address needs an actor')):
            Actor('chimera', 'fix-tests', '')


class TestFromScope:
    def test_no_project_is_the_captain(self) -> None:
        compare(Address.from_scope(Scope(WORKSPACE, None, None)), expected=Captain())

    def test_project_no_goal_is_the_manager(self) -> None:
        compare(
            Address.from_scope(Scope(WORKSPACE, _project(), None)),
            expected=Manager(project='chimera'),
        )

    def test_project_and_goal_is_the_actor(self) -> None:
        compare(
            Address.from_scope(Scope(WORKSPACE, _project(), 'fix-tests'), actor='agent'),
            expected=Actor('chimera', 'fix-tests', 'agent'),
        )

    def test_goal_with_no_actor_raises(self) -> None:
        with ShouldRaise(ValueError('from_scope: goal is set but no actor was given')):
            Address.from_scope(Scope(WORKSPACE, _project(), 'fix-tests'))
