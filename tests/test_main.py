import sys
from typing import cast

import pytest
from testfixtures import Replacer, compare, not_there
from typer._click.core import Command
from typer.core import TyperGroup
from typer.main import get_command

from chimera.__main__ import _strip_restricted_options, app, main


def _leaf(root: Command, *path: str) -> Command:
    command = root
    for name in path:
        command = cast(TyperGroup, command).commands[name]
    return command


def _option_names(command: Command) -> set[str]:
    return {opt for p in command.params for opt in p.opts}


class TestStripRestrictedOptions:
    def test_removes_force_from_worktree_rm(self) -> None:
        command = get_command(app)
        _strip_restricted_options(command)
        rm = _leaf(command, 'worktree', 'rm')
        assert '--force' not in _option_names(rm)

    def test_removes_force_from_goal_finish(self) -> None:
        command = get_command(app)
        _strip_restricted_options(command)
        finish = _leaf(command, 'goal', 'finish')
        assert '--force' not in _option_names(finish)

    def test_removes_dangerous_from_goal_start(self) -> None:
        command = get_command(app)
        _strip_restricted_options(command)
        start = _leaf(command, 'goal', 'start')
        assert '--dangerous' not in _option_names(start)

    def test_leaves_unrelated_options_alone(self) -> None:
        command = get_command(app)
        _strip_restricted_options(command)
        rm = _leaf(command, 'worktree', 'rm')
        assert {'--offline', '--dry', '--project', '-p'} <= _option_names(rm)


class TestMain:
    def test_force_unrecognized_under_agent_context(
        self, replace: Replacer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        replace.in_environ('CLAUDECODE', '1')
        replace(
            target=sys.argv,
            container=sys,
            name='argv',
            replacement=['ch', 'worktree', 'rm', 'somegoal', '--force'],
        )
        with pytest.raises(SystemExit) as excinfo:
            main()
        compare(excinfo.value.code, expected=2)
        # Rich styles "--force" as separate colored spans, splitting the literal
        # substring — assert on the unstyled lead-in text instead.
        assert 'No such option' in capsys.readouterr().err

    def test_force_recognized_without_agent_context(
        self, replace: Replacer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        replace.in_environ('CLAUDECODE', not_there)
        replace(
            target=sys.argv,
            container=sys,
            name='argv',
            replacement=['ch', 'worktree', 'rm', '--help'],
        )
        with pytest.raises(SystemExit) as excinfo:
            main()
        compare(excinfo.value.code, expected=0)
        # same styling caveat as above — assert on the help text, not the flag itself.
        assert 'Skip the live-agent check' in capsys.readouterr().out
