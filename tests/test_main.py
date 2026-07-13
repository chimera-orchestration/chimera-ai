import json
import os
import sys
from pathlib import Path
from typing import cast

import pytest
from testfixtures import Replacer, ShouldRaise, TempDir, compare, not_there
from typer._click.core import Command, Context
from typer.core import TyperCommand, TyperGroup
from typer.main import get_command

from chimera import __main__ as chimera_main
from chimera.__main__ import (
    _strip_restricted_commands,
    _strip_restricted_options,
    _strip_to_role,
    app,
    main,
)
from chimera.agent_env import ROLE_AGENT, ROLE_COMMANDS, ROLE_MANAGER, RESTRICTED_COMMANDS
from tests.cli import Command as CliCommand
from tests.cli import action_logs, leaves


def _leaf(root: Command, *path: str) -> Command:
    command = root
    for name in path:
        command = cast(TyperGroup, command).commands[name]
    return command


def _option_names(command: Command) -> set[str]:
    return {opt for p in command.params for opt in p.opts}


def _role_tree(role: str) -> TyperGroup:
    command = get_command(app)
    _strip_to_role(command, ROLE_COMMANDS[role])
    return cast(TyperGroup, command)


def _argv(replace: Replacer, *argv: str) -> None:
    replace(target=sys.argv, container=sys, name='argv', replacement=['ch', *argv])


def _completion_request(replace: Replacer, line: str = 'ch ') -> None:
    """A bash TAB against ``line``: Click dispatches completion instead of running a command."""
    replace.in_environ('_CH_COMPLETE', 'complete_bash')
    replace.in_environ('COMP_WORDS', line)
    replace.in_environ('COMP_CWORD', '1')
    _argv(replace)


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


class TestStripRestrictedCommands:
    def test_removes_logtail(self) -> None:
        command = cast(TyperGroup, get_command(app))
        assert 'logtail' in command.commands  # present for a human…
        _strip_restricted_commands(command)
        assert 'logtail' not in command.commands  # …absent for any AI session

    def test_leaves_the_rest_of_the_tree_alone(self) -> None:
        command = get_command(app)
        before = {path for path, _ in leaves(command)}
        _strip_restricted_commands(command)
        compare(before - {path for path, _ in leaves(command)}, expected=RESTRICTED_COMMANDS)

    def test_a_group_emptied_by_the_strip_goes_with_it(self, replace: Replacer) -> None:
        # no restricted command is grouped today — prove the sweep on a synthetic tree so
        # a future `log tail`-shaped entry can't leave an empty husk behind
        replace(
            target=chimera_main.RESTRICTED_COMMANDS,
            container=chimera_main,
            name='RESTRICTED_COMMANDS',
            replacement=frozenset({'log tail'}),
        )
        root = TyperGroup(
            name='ch',
            commands={'log': TyperGroup(name='log', commands={'tail': TyperCommand(name='tail')})},
        )
        _strip_restricted_commands(root)
        compare(root.commands, expected={})

    def test_every_restricted_command_names_a_live_leaf(self) -> None:
        # a stale entry (a renamed/retired command) would silently restrict nothing
        live = {path for path, _ in leaves(get_command(app))}
        compare(RESTRICTED_COMMANDS - live, expected=set())

    def test_no_role_allowlist_grants_a_restricted_command(self) -> None:
        # the role prune runs first, so an allowlist entry here would be stripped anyway —
        # but listing one would misdocument the role; keep the two sets disjoint
        for role, allowed in ROLE_COMMANDS.items():
            compare(allowed & RESTRICTED_COMMANDS, expected=set(), prefix=role)


class TestMain:
    def test_logtail_unrecognized_under_agent_context(
        self, replace: Replacer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        replace.in_environ('CLAUDECODE', '1')
        _argv(replace, 'logtail')
        with pytest.raises(SystemExit) as excinfo:
            main()
        compare(excinfo.value.code, expected=2)
        assert 'No such command' in capsys.readouterr().err

    def test_logtail_recognized_without_agent_context(
        self, replace: Replacer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        replace.in_environ('CLAUDECODE', not_there)
        _argv(replace, 'logtail', '--help')
        with pytest.raises(SystemExit) as excinfo:
            main()
        compare(excinfo.value.code, expected=0)
        assert 'Initial lines to show' in capsys.readouterr().out

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
        compare(
            set(tree.commands),
            expected={'help', 'prime', 'ls', 'review', 'errand', 'goal', 'agent', 'msg'},
        )
        goal = cast(TyperGroup, _leaf(tree, 'goal'))
        compare(
            set(goal.commands),
            expected={'start', 'adopt', 'sync', 'merge', 'pr', 'finish', 'rename', 'ls'},
        )
        compare(
            set(cast(TyperGroup, _leaf(tree, 'agent')).commands),
            expected={'start', 'resume', 'stop', 'ls'},
        )
        compare(
            set(cast(TyperGroup, _leaf(tree, 'msg')).commands),
            expected={'ls', 'send', 'inbox', 'thread', 'ack', 'defer'},
        )

    def test_agent_tree_is_help_prime_errand_and_the_mail_verbs(self) -> None:
        tree = _role_tree(ROLE_AGENT)
        compare(set(tree.commands), expected={'help', 'prime', 'errand', 'msg'})
        compare(
            set(cast(TyperGroup, _leaf(tree, 'msg')).commands),
            expected={'ls', 'send', 'inbox', 'thread', 'ack', 'defer'},
        )

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
        live = {path for path, _ in leaves(get_command(app))}
        for role, allowed in ROLE_COMMANDS.items():
            compare(allowed - live, expected=set(), prefix=role)

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

    def test_goal_traversal_cannot_escape_the_fence(
        self, tmpdir: TempDir, replace: Replacer, command: CliCommand
    ) -> None:
        # the fence checks the resolved *project*, so a -g that path-escaped the project's
        # worktrees dir would slip past it — name validation refuses before any path is built
        _fenced_manager(tmpdir, replace)
        bad = '../../other/worktrees/g'
        message = (
            f'{bad!r} is not a valid goal name: no path separators — '
            "goal names are single path segments, like 'feature-x' or 'pr-123'"
        )
        command.run('agent', 'start', '-g', bad, '--dry').check(
            output=f'Error: {message}',
            return_code=1,
            logging=action_logs(
                'agent start',
                'chimera.commands.agent.agent',
                {
                    'prompt': None,
                    'goal': bad,
                    'actor': None,
                    'project': None,
                    'dangerous': False,
                    'harness': None,
                    'model': None,
                    'dry': True,
                },
                error=f'UserError: {message}',
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
        with ShouldRaise(SystemExit(2)):
            main()
        assert 'No such command' in capsys.readouterr().err

    def test_agent_help_lists_only_allowed_leaves(
        self, tmpdir: TempDir, replace: Replacer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        replace.in_environ('CHIMERA_ROLE', 'agent')
        _argv(replace, 'help', '--json')
        with ShouldRaise(SystemExit(0)):
            main()
        entries = json.loads(capsys.readouterr().out)
        compare({entry['path'] for entry in entries}, expected=ROLE_COMMANDS[ROLE_AGENT])

    def test_unknown_role_fails_hard_before_any_command_parses(
        self, replace: Replacer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        replace.in_environ('CHIMERA_ROLE', 'bogus')
        _argv(replace, 'help')
        with ShouldRaise(SystemExit(1)):
            main()
        compare(
            capsys.readouterr().err,
            expected="Error: unknown CHIMERA_ROLE 'bogus' (known: captain, manager, agent)\n",
        )

    def test_unknown_role_completes_nothing_silently(
        self, replace: Replacer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # a stale role stamp must not break every TAB — fail closed, never loud
        replace.in_environ('CHIMERA_ROLE', 'bogus')
        _completion_request(replace)
        with ShouldRaise(SystemExit(0)):
            main()
        captured = capsys.readouterr()
        compare(captured.out, expected='')
        compare(captured.err, expected='')

    def test_listed_role_completes_within_its_stripped_tree(
        self, replace: Replacer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        replace.in_environ('CHIMERA_ROLE', ROLE_MANAGER)
        _completion_request(replace)
        with ShouldRaise(SystemExit(0)):
            main()
        compare(
            set(capsys.readouterr().out.splitlines()),
            # the manager's pruned root, plus ls's surviving synonym — nothing else
            expected={'help', 'prime', 'ls', 'list', 'review', 'errand', 'goal', 'agent', 'msg'},
        )

    def test_captain_keeps_the_full_tree_with_options_stripped(
        self, replace: Replacer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        replace.in_environ('CLAUDECODE', '1')
        replace.in_environ('CHIMERA_ROLE', 'captain')
        _argv(replace, 'worktree', 'rm', 'somegoal', '--force')
        with ShouldRaise(SystemExit(2)):
            main()
        # the command itself survived (only a role in ROLE_COMMANDS is pruned) — the
        # error is about the agent-restricted option, which still gets stripped
        assert 'No such option' in capsys.readouterr().err

    def test_captain_still_loses_human_only_commands(
        self, replace: Replacer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # the role stamp alone marks the AI session — no CLAUDECODE needed (conftest clears it)
        replace.in_environ('CHIMERA_ROLE', 'captain')
        _argv(replace, 'logtail')
        with ShouldRaise(SystemExit(2)):
            main()
        assert 'No such command' in capsys.readouterr().err

    def test_manager_command_present_with_restricted_option_stripped(
        self, replace: Replacer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        replace.in_environ('CLAUDECODE', '1')
        replace.in_environ('CHIMERA_ROLE', 'manager')
        _argv(replace, 'goal', 'finish', 'somegoal', '--force')
        with ShouldRaise(SystemExit(2)):
            main()
        assert 'No such option' in capsys.readouterr().err

    def test_role_stamp_alone_strips_restricted_options(
        self, replace: Replacer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # a future non-claude harness sets no CLAUDECODE (conftest clears it) — the role
        # stamp alone must still fence the options, never hand --force back to the session
        replace.in_environ('CHIMERA_ROLE', 'manager')
        _argv(replace, 'goal', 'finish', 'somegoal', '--force')
        with ShouldRaise(SystemExit(2)):
            main()
        assert 'No such option' in capsys.readouterr().err
