import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from testfixtures import Replacer, TempDir, compare, not_there
from typer._click.core import Command, Context
from typer.core import TyperGroup
from typer.main import get_command

from chimera.__main__ import _strip_restricted_options, _strip_to_role, app, main
from chimera.agent_env import ROLE_AGENT, ROLE_COMMANDS, ROLE_MANAGER
from tests.cli import Command as CliCommand
from tests.cli import action_logs


def _leaf(root: Command, *path: str) -> Command:
    command = root
    for name in path:
        command = cast(TyperGroup, command).commands[name]
    return command


def _option_names(command: Command) -> set[str]:
    return {opt for p in command.params for opt in p.opts}


def _leaf_paths(command: Command, path: str = '') -> Iterator[str]:
    subs = cast('dict[str, Command] | None', getattr(command, 'commands', None))
    if subs is None:
        yield path.strip()
    else:
        for name, sub in subs.items():
            yield from _leaf_paths(sub, f'{path}{name} ')


def _role_tree(role: str) -> TyperGroup:
    command = get_command(app)
    _strip_to_role(command, ROLE_COMMANDS[role])
    return cast(TyperGroup, command)


def _argv(replace: Replacer, *argv: str) -> None:
    replace(target=sys.argv, container=sys, name='argv', replacement=['ch', *argv])


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
        _argv(replace, 'worktree', 'rm', 'somegoal', '--force')
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
        _argv(replace, 'worktree', 'rm', '--help')
        with pytest.raises(SystemExit) as excinfo:
            main()
        compare(excinfo.value.code, expected=0)
        # same styling caveat as above — assert on the help text, not the flag itself.
        assert 'Skip the live-agent check' in capsys.readouterr().out


class TestStripToRole:
    def test_manager_tree_is_the_within_project_lifecycle(self) -> None:
        tree = _role_tree(ROLE_MANAGER)
        # chat/init/doctor gone; project and worktree emptied by the prune, so gone whole
        compare(set(tree.commands), expected={'help', 'prime', 'ls', 'review', 'goal', 'agent'})
        goal = cast(TyperGroup, _leaf(tree, 'goal'))
        compare(set(goal.commands), expected={'start', 'adopt', 'sync', 'finish', 'rename', 'ls'})
        compare(
            set(cast(TyperGroup, _leaf(tree, 'agent')).commands), expected={'start', 'resume', 'ls'}
        )

    def test_agent_tree_is_exactly_help_and_prime(self) -> None:
        compare(set(_role_tree(ROLE_AGENT).commands), expected={'help', 'prime'})

    def test_synonyms_survive_iff_their_canonical_does(self) -> None:
        manager = _role_tree(ROLE_MANAGER)
        goal = cast(TyperGroup, _leaf(manager, 'goal'))
        # goal start survives for a manager, so its synonym still dispatches…
        assert goal.get_command(Context(goal, info_name='goal'), 'new') is not None
        agent_tree = _role_tree(ROLE_AGENT)
        # …while the agent's stripped `ls` takes root-level `list` down with it
        assert agent_tree.get_command(Context(agent_tree, info_name='ch'), 'list') is None

    def test_every_role_command_names_a_live_leaf(self) -> None:
        # a stale allowlist entry (a renamed/retired command) would silently allow nothing
        leaves = set(_leaf_paths(get_command(app)))
        for role, allowed in ROLE_COMMANDS.items():
            compare(allowed - leaves, expected=set(), prefix=role)

    def test_role_strip_composes_with_the_option_strip(self) -> None:
        command = get_command(app)
        _strip_restricted_options(command)
        _strip_to_role(command, ROLE_COMMANDS[ROLE_MANAGER])
        finish = _leaf(command, 'goal', 'finish')  # present for a manager…
        assert '--force' not in _option_names(finish)  # …with the restricted option gone


def _fenced_manager(tmpdir: TempDir, replace: Replacer) -> Path:
    """A workspace with two projects, the session fenced to 'proj' as its manager."""
    ws = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    for name in ('proj', 'other'):
        project = ws / name
        (project / 'worktrees' / 'g@agent').mkdir(parents=True)
        tmpdir.dump(f'lycia/{name}/config.yaml', {'kind': 'project', 'repo': str(project)})
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_ROLE', ROLE_MANAGER)
    replace.in_environ('CHIMERA_ROLE_SCOPE', 'proj')
    os.chdir(ws / 'proj')
    return ws


class TestScopeFence:
    def test_lister_crosses_the_fence(
        self, tmpdir: TempDir, replace: Replacer, command: CliCommand
    ) -> None:
        _fenced_manager(tmpdir, replace)
        # cross-project listing is knowledge, not capability — never fenced
        command.run('goal', 'ls', '-p', 'other').check(
            output='g',
            logging=action_logs(
                'goal ls', 'chimera.commands.goal.ls.goals_in_scope', {'project': 'other'}
            ),
        )

    def test_action_with_explicit_project_refuses(
        self, tmpdir: TempDir, replace: Replacer, command: CliCommand
    ) -> None:
        _fenced_manager(tmpdir, replace)
        command.run('worktree', 'ls', '-p', 'other').check(
            output='Error: scoped to proj; ask the captain',
            return_code=1,
            logging=action_logs(
                'worktree ls',
                'chimera.commands.worktree.ls.ls',
                {'project': 'other'},
                error='CrossScopeError: scoped to proj; ask the captain',
            ),
        )

    def test_action_in_scope_proceeds(
        self, tmpdir: TempDir, replace: Replacer, command: CliCommand
    ) -> None:
        _fenced_manager(tmpdir, replace)
        command.run('worktree', 'ls').check(
            output=str(Path.cwd() / 'worktrees' / 'g@agent'),
            logging=action_logs(
                'worktree ls', 'chimera.commands.worktree.ls.ls', {'project': None}
            ),
        )

    def test_cwd_inferred_cross_scope_refuses(
        self, tmpdir: TempDir, replace: Replacer, command: CliCommand
    ) -> None:
        # the fence checks the *resolved* project: standing in another project's dir
        # refuses exactly like an explicit -p
        ws = _fenced_manager(tmpdir, replace)
        os.chdir(ws / 'other')
        command.run('worktree', 'ls').check(
            output='Error: scoped to proj; ask the captain',
            return_code=1,
            logging=action_logs(
                'worktree ls',
                'chimera.commands.worktree.ls.ls',
                {'project': None},
                error='CrossScopeError: scoped to proj; ask the captain',
            ),
        )


class TestMainRole:
    def test_manager_cannot_reach_project_add(
        self, replace: Replacer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        replace.in_environ('CHIMERA_ROLE', 'manager')
        _argv(replace, 'project', 'add', 'https://example.com/r.git')
        with pytest.raises(SystemExit) as excinfo:
            main()
        compare(excinfo.value.code, expected=2)
        assert 'No such command' in capsys.readouterr().err

    def test_agent_help_lists_only_allowed_leaves(
        self, tmpdir: TempDir, replace: Replacer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        replace.in_environ('CHIMERA_ROLE', 'agent')
        _argv(replace, 'help', '--json')
        with pytest.raises(SystemExit) as excinfo:
            main()
        compare(excinfo.value.code, expected=0)
        entries = json.loads(capsys.readouterr().out)
        compare({entry['path'] for entry in entries}, expected=ROLE_COMMANDS[ROLE_AGENT])

    def test_unknown_role_fails_hard_before_any_command_parses(
        self, replace: Replacer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        replace.in_environ('CHIMERA_ROLE', 'bogus')
        _argv(replace, 'help')
        with pytest.raises(SystemExit) as excinfo:
            main()
        compare(excinfo.value.code, expected=1)
        compare(
            capsys.readouterr().err,
            expected="Error: unknown CHIMERA_ROLE 'bogus' (known: captain, manager, agent)\n",
        )

    def test_captain_keeps_the_full_tree_with_options_stripped(
        self, replace: Replacer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        replace.in_environ('CLAUDECODE', '1')
        replace.in_environ('CHIMERA_ROLE', 'captain')
        _argv(replace, 'worktree', 'rm', 'somegoal', '--force')
        with pytest.raises(SystemExit) as excinfo:
            main()
        compare(excinfo.value.code, expected=2)
        # the command itself survived (only a role in ROLE_COMMANDS is pruned) — the
        # error is about the agent-restricted option, which still gets stripped
        assert 'No such option' in capsys.readouterr().err

    def test_manager_command_present_with_restricted_option_stripped(
        self, replace: Replacer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        replace.in_environ('CLAUDECODE', '1')
        replace.in_environ('CHIMERA_ROLE', 'manager')
        _argv(replace, 'goal', 'finish', 'somegoal', '--force')
        with pytest.raises(SystemExit) as excinfo:
            main()
        compare(excinfo.value.code, expected=2)
        assert 'No such option' in capsys.readouterr().err
