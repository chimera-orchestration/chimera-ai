import os
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path

from giterator import Git
from giterator.testing import Repo
from testfixtures import Replacer, ShouldRaise, TempDir, compare

from chimera.agent_env import ROLE_AGENT
from chimera.agents.registry import AgentSpec
from chimera.config import UserError
from chimera.dry import Dry
from chimera.commands.agent import agent
from chimera.commands.goal import start as goal_start
from chimera.commands.goal.start import start
from chimera.prime import prime
from tests.cli import Command, action_logs, context_sources, sources_lines


def _project(tmpdir: TempDir, repo: Repo) -> Path:
    project = tmpdir.makedir('project')
    tmpdir.dump('project/config.yaml', {'kind': 'project', 'repo': str(repo.path)})
    os.chdir(project)  # the CLI infers the project (and its name) from cwd
    return project


def _stub_agent(replace: Replacer) -> list[object]:
    calls: list[object] = []

    def record(
        worktree: Path,
        name: str,
        prompt: str | None = None,
        extra: Sequence[str] = (),
        dangerous: bool = False,
        spec: AgentSpec = AgentSpec(),
        context: Path | None = None,
        env: Mapping[str, str] = {},
        dry: Dry = Dry(),
    ) -> None:
        calls.append((worktree, name, prompt, extra, dangerous, spec, context, env))

    replace.in_module(agent, record, module=goal_start)
    return calls


def test_start_dry_creates_nothing(tmpdir: TempDir, git_repo: Repo, replace: Replacer) -> None:
    worktrees = tmpdir / 'worktrees'
    calls = _stub_agent(replace)
    compare(
        start(git_repo.path, worktrees, 'g', 'proj@g@agent', dry=Dry(True)),
        expected=worktrees / 'g@agent',
    )
    assert not worktrees.exists()  # no worktree dir
    compare(Git(git_repo.path).branches(), expected=['main'])  # no branches
    compare(len(calls), expected=1)  # the launch call still flows (itself dry-routed)


def test_start_refuses_a_traversal_goal_even_dry(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    worktrees = tmpdir / 'worktrees'
    calls = _stub_agent(replace)
    # validation sits ahead of the Dry guard, so --dry can't preview an escaping path as fine
    with ShouldRaise(
        UserError(
            "'../../x' is not a valid goal name: no path separators — "
            "goal names are single path segments, like 'feature-x' or 'pr-123'"
        )
    ):
        start(git_repo.path, worktrees, '../../x', 'proj@../../x@agent', dry=Dry(True))
    assert not worktrees.exists()
    compare(calls, expected=[])


def test_start_creates_worktrees_then_launches_the_agent(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    worktrees = tmpdir / 'worktrees'
    calls = _stub_agent(replace)
    compare(start(git_repo.path, worktrees, 'g', 'proj@g@agent'), expected=worktrees / 'g@agent')
    tmpdir.compare(['g@agent'], path='worktrees', recursive=False)
    compare(Git(git_repo.path).branches(), expected=['g/agent', 'main'])  # human is lazy
    # foreground (no prompt), not dangerous
    compare(
        calls,
        expected=[(worktrees / 'g@agent', 'proj@g@agent', None, (), False, AgentSpec(), None, {})],
    )


def test_start_passes_the_prompt_to_the_agent(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    worktrees = tmpdir / 'worktrees'
    calls = _stub_agent(replace)
    start(git_repo.path, worktrees, 'g', 'proj@g@agent', prompt='do it')
    compare(
        calls,
        expected=[
            (worktrees / 'g@agent', 'proj@g@agent', 'do it', (), False, AgentSpec(), None, {})
        ],
    )


def test_start_passes_dangerous_to_the_agent(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer
) -> None:
    worktrees = tmpdir / 'worktrees'
    calls = _stub_agent(replace)
    start(git_repo.path, worktrees, 'g', 'proj@g@agent', dangerous=True)
    compare(
        calls,
        expected=[(worktrees / 'g@agent', 'proj@g@agent', None, (), True, AgentSpec(), None, {})],
    )


def test_goal_start_cli(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, git_repo)
    calls = _stub_agent(replace)  # stub the agent so real git runs but no claude launches
    expected = Path.cwd() / 'worktrees' / 'feature-x@agent'
    command.run('goal', 'start', 'feature-x').check(
        output=f'Started feature-x in {expected}',
        logging=action_logs(
            'goal start',
            'chimera.commands.goal.start.start',
            {
                'goal': 'feature-x',
                'prompt': None,
                'frm': None,
                'project': None,
                'dangerous': False,
                'harness': None,
                'model': None,
                'dry': False,
                'offline': False,
            },
        ),
    )
    tmpdir.compare(['feature-x@agent'], path='project/worktrees', recursive=False)
    compare(
        calls,
        expected=[
            (
                expected,
                'project@feature-x@agent',
                None,
                [],
                False,
                AgentSpec(),
                None,
                {'CHIMERA_ROLE': 'agent', 'CHIMERA_ROLE_SCOPE': 'project@feature-x'},
            )
        ],
    )


def test_goal_start_cli_with_prompt(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, git_repo)
    calls = _stub_agent(replace)
    expected = Path.cwd() / 'worktrees' / 'feature-x@agent'
    command.run('goal', 'start', 'feature-x', 'go build it').check(
        output=f'Started feature-x in {expected}',
        logging=action_logs(
            'goal start',
            'chimera.commands.goal.start.start',
            {
                'goal': 'feature-x',
                'prompt': 'go build it',
                'frm': None,
                'project': None,
                'dangerous': False,
                'harness': None,
                'model': None,
                'dry': False,
                'offline': False,
            },
        ),
    )
    compare(
        calls,
        expected=[
            (
                expected,
                'project@feature-x@agent',
                'go build it',
                [],
                False,
                AgentSpec(),
                None,
                {'CHIMERA_ROLE': 'agent', 'CHIMERA_ROLE_SCOPE': 'project@feature-x'},
            )
        ],
    )


def test_goal_start_cli_dangerous(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, git_repo)
    calls = _stub_agent(replace)
    expected = Path.cwd() / 'worktrees' / 'feature-x@agent'
    command.run('goal', 'start', 'feature-x', '--dangerous').check(
        output=f'Started feature-x in {expected}',
        logging=action_logs(
            'goal start',
            'chimera.commands.goal.start.start',
            {
                'goal': 'feature-x',
                'prompt': None,
                'frm': None,
                'project': None,
                'dangerous': True,
                'harness': None,
                'model': None,
                'dry': False,
                'offline': False,
            },
        ),
    )
    compare(
        calls,
        expected=[
            (
                expected,
                'project@feature-x@agent',
                None,
                [],
                True,
                AgentSpec(),
                None,
                {'CHIMERA_ROLE': 'agent', 'CHIMERA_ROLE_SCOPE': 'project@feature-x'},
            )
        ],
    )


def test_goal_start_cli_offline(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, git_repo)
    calls = _stub_agent(replace)
    expected = Path.cwd() / 'worktrees' / 'feature-x@agent'
    command.run('goal', 'start', 'feature-x', '--offline').check(
        output=f'Started feature-x in {expected}',
        logging=action_logs(
            'goal start',
            'chimera.commands.goal.start.start',
            {
                'goal': 'feature-x',
                'prompt': None,
                'frm': None,
                'project': None,
                'dangerous': False,
                'harness': None,
                'model': None,
                'dry': False,
                'offline': True,
            },
        ),
    )
    compare(
        calls,
        expected=[
            (
                expected,
                'project@feature-x@agent',
                None,
                [],
                False,
                AgentSpec(),
                None,
                {'CHIMERA_ROLE': 'agent', 'CHIMERA_ROLE_SCOPE': 'project@feature-x'},
            )
        ],
    )


def test_goal_start_cli_passes_extra_flags_through(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, git_repo)
    calls = _stub_agent(replace)
    expected = Path.cwd() / 'worktrees' / 'feature-x@agent'
    command.run('goal', 'start', 'feature-x', '--', '--model', 'opus').check(
        output=f'Started feature-x in {expected}',
        logging=action_logs(
            'goal start',
            'chimera.commands.goal.start.start',
            {
                'goal': 'feature-x',
                'prompt': None,
                'frm': None,
                'project': None,
                'dangerous': False,
                'harness': None,
                'model': None,
                'dry': False,
                'offline': False,
            },
        ),
    )
    compare(
        calls,
        expected=[
            (
                expected,
                'project@feature-x@agent',
                None,
                ['--model', 'opus'],
                False,
                AgentSpec(),
                None,
                {'CHIMERA_ROLE': 'agent', 'CHIMERA_ROLE_SCOPE': 'project@feature-x'},
            )
        ],
    )


def test_goal_start_cli_dry_role_leads_the_context(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    ws = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    # the project's own roles/agent/ layer reaches the launched agent's context
    tmpdir.write(ws / 'proj' / 'roles' / 'agent' / 'persona.md', 'Guard the reactor.\n')
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    os.chdir(ws / 'proj')
    worktree = Path.cwd() / 'worktrees' / 'g@agent'  # cwd resolves symlinks like the wrapper
    persona = (Path.cwd() / 'roles' / 'agent' / 'persona.md').resolve()
    text = (
        f'# Role: agent\n\n{prime(ROLE_AGENT, project="proj", goal="g")}\n\n'
        f'<!-- {persona} (project) -->\nGuard the reactor.'
    )
    digest = sha256(text.encode()).hexdigest()
    context = ws / 'state' / 'context' / f'proj@g@agent-{digest[:8]}.md'
    sources = context_sources(ws, 'agent', pinned=Path.cwd())
    sources[str(Path.cwd() / 'roles' / 'agent' / '*.md')] = [str(persona)]
    command.run('goal', 'start', 'g', '--dry').check(
        output='\n'.join(
            [
                f'Would start g in {worktree}',
                'harness: claude',
                'role: agent (scope: proj@g)',
                'prompt: (interactive)',
                *sources_lines(sources),
                f'context: {context}',
                '---',
                text,
            ]
        ),
        logging=[
            {
                'level': 'INFO',
                'command': 'goal start',
                'goal': 'g',
                'phase': 'start',
                'function': 'chimera.commands.goal.start.start',
                'params': {
                    'goal': 'g',
                    'prompt': None,
                    'frm': None,
                    'offline': False,
                    'dangerous': False,
                    'harness': None,
                    'model': None,
                    'dry': True,
                    'project': None,
                },
            },
            {
                'level': 'INFO',
                'goal': 'g',
                'session': 'proj@g@agent',
                'path': str(context),
                'sha256': digest,
                'sources': sources,
                'message': 'context: rendered',
            },
            {'level': 'INFO', 'command': 'goal start', 'goal': 'g', 'phase': 'end'},
        ],
    )
    assert not (ws / 'proj' / 'worktrees').exists()  # nothing created
    compare(Git(git_repo.path).branches(), expected=['main'])


def test_goal_start_cli_dry(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    project = _project(tmpdir, git_repo)
    worktree = Path.cwd() / 'worktrees' / 'g@agent'  # cwd resolves symlinks like the wrapper
    command.run('goal', 'start', 'g', '--dry').check(
        output='\n'.join(
            [
                f'Would start g in {worktree}',
                'harness: claude',
                'role: agent (scope: project@g)',
                'prompt: (interactive)',
                'context: (none)',
            ]
        ),
        logging=action_logs(
            'goal start',
            'chimera.commands.goal.start.start',
            {
                'goal': 'g',
                'prompt': None,
                'frm': None,
                'offline': False,
                'dangerous': False,
                'harness': None,
                'model': None,
                'dry': True,
                'project': None,
            },
        ),
    )
    assert not (project / 'worktrees').exists()  # nothing created
    compare(Git(git_repo.path).branches(), expected=['main'])


def test_goal_start_cli_dry_refuses_a_traversal_goal(
    tmpdir: TempDir, git_repo: Repo, command: Command
) -> None:
    project = _project(tmpdir, git_repo)
    message = (
        "'../../x' is not a valid goal name: no path separators — "
        "goal names are single path segments, like 'feature-x' or 'pr-123'"
    )
    command.run('goal', 'start', '../../x', '--dry').check(
        output=f'Error: {message}',
        return_code=1,
        logging=action_logs(
            'goal start',
            'chimera.commands.goal.start.start',
            {
                'goal': '../../x',
                'prompt': None,
                'frm': None,
                'offline': False,
                'dangerous': False,
                'harness': None,
                'model': None,
                'dry': True,
                'project': None,
            },
            error=f'UserError: {message}',
        ),
    )
    assert not (project / 'worktrees').exists()  # nothing created
    compare(Git(git_repo.path).branches(), expected=['main'])
