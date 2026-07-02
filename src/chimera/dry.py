from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Dry:
    """A dry-run switch threaded through a destructive action.

    Read-only work — discovery, safety checks — runs regardless; only the mutating
    calls made *through* this switch are skipped when it is ``on``. So ``--dry`` shares
    a single code path with the real run: the same guards fire and the same steps are
    chosen, so a preview can never drift from what the command would actually do. Reuse
    it for any future destructive command — take a ``Dry`` and route each mutation through
    it. Off by default, so a plain call runs everything.

    Guard a mutation by calling the switch with it (immediately, so loop variables bind
    as expected — nothing is deferred)::

        dry(git, 'branch', '-D', ref)   # runs the delete, unless this is a dry run
        dry(shutil.rmtree, project)

    and pick the reporting word with :meth:`verb`.
    """

    on: bool = False

    def __call__[**P](self, mutate: Callable[P, object], *args: P.args, **kwargs: P.kwargs) -> None:
        """Run ``mutate(*args, **kwargs)`` unless this is a dry run."""
        if not self.on:
            mutate(*args, **kwargs)

    def verb(self, ran: str, would: str) -> str:
        """``would`` under a dry run, else ``ran`` — for reporting what did/would happen."""
        return would if self.on else ran
