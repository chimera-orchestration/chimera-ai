"""Pin doc-kept enumerations to the live command tree, so they can't drift."""

import re
from collections.abc import Iterator
from pathlib import Path

from testfixtures import compare
from typer._click.core import Command
from typer.core import TyperGroup
from typer.main import get_command

from chimera.__main__ import PassthroughCommand, app

WORKSPACE_LAYOUT = Path(__file__).parent.parent / 'agent-docs' / 'workspace-layout.md'

# The doc's single launcher list: "Every launching command (`agent start`/`resume`, …)".
LAUNCHERS = re.compile(r'Every launching command \(([^)]*)\)')


def _leaves(command: Command, path: str = '') -> Iterator[tuple[str, Command]]:
    if isinstance(command, TyperGroup):
        for name, sub in command.commands.items():
            yield from _leaves(sub, f'{path}{name} ')
    else:
        yield path.strip(), command


def _documented_launchers(text: str) -> set[str]:
    """The command paths in the doc's one enumeration, `a b`/`c` expanding to {a b, a c}."""
    match = LAUNCHERS.search(text)
    assert match is not None
    launchers: set[str] = set()
    for group in match.group(1).split(', '):
        first, *rest = re.findall(r'`([^`]+)`', group)
        launchers.add(first)
        prefix = first.rsplit(' ', 1)[0] + ' ' if ' ' in first else ''
        launchers.update(f'{prefix}{sibling}' for sibling in rest)
    return launchers


def test_launcher_list_matches_the_live_tree() -> None:
    # the launchers are exactly the PassthroughCommands, and exactly the --dangerous carriers —
    # a command gaining either without joining the doc's list fails here, as does a stale entry
    leaves = dict(_leaves(get_command(app)))
    documented = _documented_launchers(WORKSPACE_LAYOUT.read_text())
    compare(
        documented,
        expected={path for path, cmd in leaves.items() if isinstance(cmd, PassthroughCommand)},
    )
    compare(
        documented,
        expected={
            path
            for path, cmd in leaves.items()
            if any('--dangerous' in param.opts for param in cmd.params)
        },
    )
