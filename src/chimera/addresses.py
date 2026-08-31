"""Typed session/mail addresses: captain, manager, goal actor.

One address grammar for everything that names a chimera session or mail recipient —
``--name``, a Maildir under ``state/mail/``, the archive's ``name`` column. Every
address is exactly three :data:`SEP`-joined segments, ``project@goal@actor``, with an
empty segment where a role has none: a captain has neither project nor goal
(``@@captain``), a manager has no goal (``<project>@@manager``), a goal actor has all
three. That uniform shape makes :meth:`Address.parse` total and unambiguous — which
segments are empty decides the type, so there is exactly one string-parsing site in
the whole codebase; everywhere else builds or reads a typed :data:`AnyAddress`.

``manager``/``captain`` are reserved: never valid as a goal actor's own name, enforced
both here (:class:`Actor`'s own construction) and in
:func:`chimera.worktrees.require_valid_actor`, sharing :data:`RESERVED_ACTORS` rather
than each hand-typing the two strings.
"""

from abc import ABC
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.context import Scope

SEP = '@'
"""Joins an address's three segments. Reused by ``chimera.worktrees`` for the (related
but distinct) worktree-dir grammar; defined here, not imported from there, so this
module has no dependency on it."""


class Address(ABC):
    """One chimera session/mail identity: ``project``, ``goal``, ``actor`` — some
    empty depending on the concrete role. Subclasses are frozen dataclasses; this
    class supplies the one shared rendering, never instantiated on its own."""

    project: str
    goal: str
    actor: str

    def render(self) -> str:
        """The address string: ``project@goal@actor``, empty segments included."""
        return SEP.join((self.project, self.goal, self.actor))

    __str__ = render

    @classmethod
    def parse(cls, raw: str) -> 'AnyAddress':
        """``raw`` as the address it names; ``ValueError`` if it names none.

        Total over the three shapes: exactly 3 :data:`SEP`-joined segments, dispatched
        by which are empty. ``Actor``'s own construction rejects a reserved ``actor``
        (see module docstring), so a would-be actor named ``manager``/``captain`` is
        refused here too, by construction rather than a second check.
        """
        parts = raw.split(SEP)
        if len(parts) != 3:
            raise ValueError(f'{raw!r} is not a valid address: expected exactly 2 {SEP!r}s')
        project, goal, actor = parts
        match (bool(project), bool(goal)):
            case (False, False):
                if actor != Captain.actor:
                    raise ValueError(f'{raw!r} is not a valid address')
                return Captain()
            case (True, False):
                if actor != Manager.actor:
                    raise ValueError(f'{raw!r} is not a valid address')
                return Manager(project=project)
            case (False, True):
                raise ValueError(f'{raw!r} is not a valid address: missing project')
            case (True, True):
                return Actor(project=project, goal=goal, actor=actor)

    @classmethod
    def from_scope(cls, scope: 'Scope', actor: str | None = None) -> 'AnyAddress':
        """The :class:`~chimera.context.Scope` a lister/hook resolved, as an address.

        ``scope.project``/``scope.goal`` are ``None`` exactly where this class's own
        fields are ``''`` — this is the one seam that absorbs that difference, so
        no concrete :class:`Address` field is ever legitimately ``None``. Raises
        ``ValueError`` (never a bare ``assert``) if the goal branch is reached with no
        ``actor`` — a caller contract violation, not a user-facing error.
        """
        if scope.project is None:
            return Captain()
        if scope.goal is None:
            return Manager(project=scope.project.name)
        if actor is None:
            raise ValueError('from_scope: goal is set but no actor was given')
        return Actor(project=scope.project.name, goal=scope.goal, actor=actor)


@dataclass(frozen=True)
class Captain(Address):
    """The workspace's captain: no project, no goal, singleton per workspace."""

    project: str = field(default='', init=False)
    goal: str = field(default='', init=False)
    actor: str = field(default='captain', init=False)


@dataclass(frozen=True)
class Manager(Address):
    """A project's manager: no goal."""

    project: str
    goal: str = field(default='', init=False)
    actor: str = field(default='manager', init=False)


@dataclass(frozen=True)
class Actor(Address):
    """A participant in a goal: ``project``, ``goal`` and the actor's own name."""

    project: str
    goal: str
    actor: str

    def __post_init__(self) -> None:
        # in the constructor rather than in `parse`, so a programmatic Actor is refused
        # too — this is the one shape whose third segment isn't a fixed literal, and so
        # the only one nothing else was checking
        if not self.actor:
            raise ValueError('an actor address needs an actor')
        if self.actor in RESERVED_ACTORS:
            raise ValueError(f'{self.actor!r} is a reserved role name, not a valid actor')


AnyAddress = Captain | Manager | Actor

RESERVED_ACTORS = (Manager.actor, Captain.actor)
"""Actor names no goal actor may take — imported by :func:`chimera.worktrees.
require_valid_actor`, which sees only a bare name and can't construct a full
:class:`Actor` to trigger its own check."""
