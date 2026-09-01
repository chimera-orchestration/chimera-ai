import os
import re
from pathlib import Path
from typing import cast

from testfixtures import Replacer, ShouldRaise, TempDir, compare
from typer._click.core import Command as ClickCommand
from typer.main import get_command

from chimera.__main__ import _strip_restricted_commands, _strip_to_role, app
from chimera.addresses import Address
from chimera.agent_env import ROLE_AGENT, ROLE_CAPTAIN, ROLE_COMMANDS, ROLE_MANAGER
from chimera.config import ProjectConfig
from chimera.context import Project, Scope
from chimera.prime import PRIMES, prime, resolve_role
from tests.cli import Command, action_logs

CITED = re.compile(r'`ch ([^`]+)`')
WORD = re.compile(r'[a-z][a-z-]*')


CITED_ADDRESS = re.compile(r'`([^`]*@[^`]*)`')
PLACEHOLDER = re.compile(r'<[a-z-]+>')


def _cited_addresses(text: str) -> set[str]:
    """Every backtick-cited address, with its ``<placeholders>`` filled by a plain name."""
    return {PLACEHOLDER.sub('x', cited) for cited in CITED_ADDRESS.findall(text)}


def _citations(text: str) -> set[tuple[str, ...]]:
    """Every backtick-cited ``ch …`` command, reduced to its leading command-word tokens."""
    citations: set[tuple[str, ...]] = set()
    for match in CITED.finditer(text):
        tokens: list[str] = []
        for token in match.group(1).split():
            if not WORD.fullmatch(token):
                break  # a flag, placeholder or quote: the command words have ended
            tokens.append(token)
        citations.add(tuple(tokens))
    return citations


def _role_tree(role: str) -> ClickCommand:
    """The command tree ``role``'s sessions actually see: the role allowlist prune for the
    listed roles (the captain skips it), then the human-only command strip every AI
    session gets — so a prime citing ``ch logtail`` fails here for every role."""
    tree = get_command(app)
    if role in ROLE_COMMANDS:
        _strip_to_role(tree, ROLE_COMMANDS[role])
    _strip_restricted_commands(tree)
    return tree


def _is_live_leaf(tree: ClickCommand, tokens: tuple[str, ...]) -> bool:
    command = tree
    for token in tokens:
        commands = cast('dict[str, ClickCommand] | None', getattr(command, 'commands', None))
        if commands is None:
            return True  # already at a leaf; the remaining tokens are its args
        if token not in commands:
            return False
        command = commands[token]
    return getattr(command, 'commands', None) is None  # a bare group is not runnable


def _assert_citations_resolve(role: str) -> None:
    citations = _citations(prime(role))
    assert citations  # the extractor found something to pin
    tree = _role_tree(role)
    unresolved = {tokens for tokens in citations if not _is_live_leaf(tree, tokens)}
    compare(unresolved, expected=set(), prefix=role)


class TestCitationsPinToTheRoleTree:
    def test_captain_citations_resolve_in_the_full_tree(self) -> None:
        _assert_citations_resolve(ROLE_CAPTAIN)

    def test_manager_citations_resolve_in_its_stripped_tree(self) -> None:
        _assert_citations_resolve(ROLE_MANAGER)

    def test_agent_citations_resolve_in_its_stripped_tree(self) -> None:
        _assert_citations_resolve(ROLE_AGENT)

    def test_agent_cites_only_help_errand_and_mail(self) -> None:
        # no orchestration commands — being managed is described, never commanded;
        # errand (read-only cross-project research) and its own mail are the verbs
        # an agent runs itself
        compare(
            _citations(prime(ROLE_AGENT)),
            expected={('help',), ('errand',), ('msg', 'ack'), ('msg', 'send'), ('msg', 'watch')},
        )

    def test_every_role_signposts_help(self) -> None:
        for role in PRIMES:
            assert '`ch help`' in prime(role), role

    def test_the_chat_roles_are_told_their_scrollback_is_not_durable(self) -> None:
        # a conversation lives in the harness's transcript, under its retention, so a
        # captain or manager will sometimes wake with none of it — its address, mail and
        # board survive, its memory does not. Nothing in chimera can change that, so the
        # roles that own long-lived conversations are told where to look instead
        for role in (ROLE_CAPTAIN, ROLE_MANAGER):
            text = prime(role, workspace='lycia', project='proj')
            assert 'retention' in text, role
            assert 'knowledge/' in text, role

    def test_the_agent_is_not(self) -> None:
        # an agent's conversation is bounded by its goal; it has no long thread to lose,
        # and pointing it at a machine-wide index invites it out of its own worktree
        assert 'agentsview' not in prime(ROLE_AGENT, project='proj', goal='g')

    def test_every_cited_address_parses(self) -> None:
        # the commands were pinned to a live tree from the start; the addresses beside
        # them were not, and the captain spent this branch teaching a grammar its own
        # `ch msg send` refuses — a fresh captain following its launch context verbatim
        for role in PRIMES:
            text = prime(role, workspace='lycia', project='proj', goal='g')
            for cited in _cited_addresses(text):
                Address.parse(cited)  # raises, naming the role, if a template drifts

    def test_the_address_pin_rejects_the_grammar_it_replaced(self) -> None:
        # the pin only works if these are the shapes it would have caught
        compare(_cited_addresses('`<project>@manager` and `pegasus`'), expected={'x@manager'})
        for stale in ('x@manager', 'pegasus'):
            with ShouldRaise(ValueError):
                Address.parse(stale)

    def test_the_pin_itself_accepts_args_and_rejects_ghosts(self) -> None:
        # the resolver the pin rides on: word tokens past a leaf are its args, and a
        # citation of a command that doesn't exist must fail the pin, not slip through
        tree = _role_tree(ROLE_CAPTAIN)
        assert _is_live_leaf(tree, ('goal', 'sync', 'somegoal'))
        assert not _is_live_leaf(tree, ('goal', 'bogus'))


def _scope(tmpdir: TempDir, *, project: bool = False, goal: str | None = None) -> Scope:
    ws = tmpdir / 'lycia'
    pinned = Project(ws / 'proj', ProjectConfig(kind='project', repo=Path('/r')))
    return Scope(ws, pinned if project or goal else None, goal)


class TestResolveRole:
    def test_env_beats_cwd(self, tmpdir: TempDir) -> None:
        compare(resolve_role(ROLE_AGENT, _scope(tmpdir)), expected=ROLE_AGENT)

    def test_goal_worktree_is_its_agent(self, tmpdir: TempDir) -> None:
        compare(resolve_role(None, _scope(tmpdir, goal='g')), expected=ROLE_AGENT)

    def test_project_dir_is_its_manager(self, tmpdir: TempDir) -> None:
        compare(resolve_role(None, _scope(tmpdir, project=True)), expected=ROLE_MANAGER)

    def test_bare_workspace_is_the_captain(self, tmpdir: TempDir) -> None:
        compare(resolve_role(None, _scope(tmpdir)), expected=ROLE_CAPTAIN)


class TestPrime:
    def test_substitutes_the_scope_names(self) -> None:
        text = prime(ROLE_AGENT, project='proj', goal='g')
        assert 'You are the agent for goal g on proj; this worktree and branch' in text

    def test_placeholders_when_unpinned(self) -> None:
        assert 'goal <goal> on <project>' in prime(ROLE_AGENT)

    def test_captain_carries_the_persona(self) -> None:
        assert prime(ROLE_CAPTAIN, persona='pegasus').startswith('You are pegasus, the captain')

    def test_captain_names_the_workspace(self) -> None:
        assert 'the captain of the lycia workspace' in prime(ROLE_CAPTAIN, workspace='lycia')
        assert 'the captain of the <workspace> workspace' in prime(ROLE_CAPTAIN)

    def test_manager_names_the_project(self) -> None:
        assert 'the manager of the proj project' in prime(ROLE_MANAGER, project='proj')


def _workspace(tmpdir: TempDir, replace: Replacer) -> Path:
    ws = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace', 'captain': 'pegasus'})
    project = ws / 'proj'
    (project / 'worktrees' / 'g@agent').mkdir(parents=True)
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(project)})
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    return ws


def test_prime_cli_captain_at_the_bare_workspace(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    os.chdir(_workspace(tmpdir, replace))
    command.run('prime').check(
        output=prime(ROLE_CAPTAIN, persona='pegasus', workspace='lycia'),
        logging=action_logs('prime', 'chimera.prime.prime', {}),
    )


def test_prime_cli_manager_in_a_project_dir(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    os.chdir(_workspace(tmpdir, replace) / 'proj')
    command.run('prime').check(
        output=prime(ROLE_MANAGER, project='proj'),
        logging=action_logs('prime', 'chimera.prime.prime', {}),
    )


def test_prime_cli_agent_in_a_goal_worktree(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    os.chdir(_workspace(tmpdir, replace) / 'proj' / 'worktrees' / 'g@agent')
    command.run('prime').check(
        output=prime(ROLE_AGENT, project='proj', goal='g'),
        logging=action_logs('prime', 'chimera.prime.prime', {}),
    )
