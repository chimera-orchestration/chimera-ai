import os
from hashlib import sha256
from collections.abc import Iterable
from pathlib import Path

from testfixtures import Replacer, ShouldRaise, TempDir, compare

from chimera.addresses import Captain, Manager
from chimera.agent_env import ROLE_CAPTAIN, ROLE_MANAGER
from chimera.agents import AgentSession
from chimera.agents.claude import Claude
from chimera.commands.chat import ChatAlreadyLiveError, GoalHasAgentError, chat, chat_target
from chimera.config import ProjectConfig, UserError
from chimera.context import Project, Scope
from chimera.dry import Dry
from chimera.prime import prime
from tests.cli import (
    Command,
    action_logs,
    capture_env,
    capture_launches,
    context_sources,
    launched,
    launching,
    SESSION_ID,
    sources_lines,
)

# the role's prime is the identity block of every chat launch context
CAPTAIN_TEXT = f'# Role: captain\n\n{prime(ROLE_CAPTAIN, persona="pegasus", workspace="lycia")}'
MANAGER_TEXT = f'# Role: manager\n\n{prime(ROLE_MANAGER, project="proj")}'


def _project_obj(directory: Path) -> Project:
    return Project(directory, ProjectConfig(kind='project', repo=Path('/r')))


def _stub(replace: Replacer, live: Iterable[AgentSession] = ()) -> list[object]:
    replace.on_class(Claude.live, lambda self, cwd=None: list(live))
    return capture_launches(replace)


class TestChatTarget:
    def test_workspace_scope_is_the_captain(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('lycia')
        compare(chat_target(Scope(ws, None, None), 'pegasus'), expected=(ws, 'pegasus'))

    def test_project_scope(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('lycia')
        project = _project_obj(ws / 'proj')
        compare(
            chat_target(Scope(ws, project, None), 'pegasus'),
            expected=(ws / 'proj', 'proj@@manager'),
        )

    def test_project_chat_name_carries_the_manager_role(self, tmpdir: TempDir) -> None:
        # the session name carries the role at every layer; a project chat is its manager
        ws = tmpdir.makedir('lycia')
        _, name = chat_target(Scope(ws, _project_obj(ws / 'proj'), None), 'pegasus')
        compare(name, expected=str(Manager(project='proj')))

    def test_goal_scope_refuses(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('lycia')
        project = _project_obj(ws / 'proj')
        with ShouldRaise(GoalHasAgentError('g', 'proj')):
            chat_target(Scope(ws, project, 'g'), 'pegasus')

    def test_explicit_goal_refuses_even_without_a_project(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('lycia')
        with ShouldRaise(GoalHasAgentError('g')):
            chat_target(Scope(ws, None, None), 'pegasus', goal='g')


class TestChat:
    def test_launches_alongside_live_sessions(self, tmpdir: TempDir, replace: Replacer) -> None:
        ws = tmpdir.makedir('lycia')
        # another session is live in the same cwd — chat launches anyway (not exclusive)
        calls = _stub(replace, live=[AgentSession('x', 'other', 'idle', ws, None)])
        assert chat(ws, 'pegasus') is None  # no note: nothing to report
        compare(calls, expected=[(['claude', '--session-id', SESSION_ID, '--name', 'pegasus'], ws)])

    def test_resume_revives_by_name(self, tmpdir: TempDir, replace: Replacer) -> None:
        ws = tmpdir.makedir('lycia')
        calls = _stub(replace)
        chat(ws, 'pegasus', resume=True)
        compare(calls, expected=[(['claude', '--resume', 'pegasus'], ws)])

    def test_refuses_when_the_chat_itself_is_live(self, tmpdir: TempDir, replace: Replacer) -> None:
        ws = tmpdir.makedir('lycia')
        calls = _stub(replace, live=[AgentSession('x', 'pegasus', 'idle', ws, None)])
        with ShouldRaise(ChatAlreadyLiveError('pegasus')):
            chat(ws, 'pegasus')
        with ShouldRaise(ChatAlreadyLiveError('pegasus')):  # resume can't attach either
            chat(ws, 'pegasus', resume=True)
        compare(calls, expected=[])  # never launched

    def test_dry_reports_the_live_chat_instead_of_refusing(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        ws = tmpdir.makedir('lycia')
        calls = _stub(replace, live=[AgentSession('x', 'pegasus', 'idle', ws, None)])
        note = "note: chat 'pegasus' is already live — a real launch would refuse"
        compare(chat(ws, 'pegasus', dry=Dry(True)), expected=note)
        compare(chat(ws, 'pegasus', resume=True, dry=Dry(True)), expected=note)
        compare(calls, expected=[])  # previewed, never launched

    def test_extra_bypass_flags_refused_under_an_ai_agent(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        ws = tmpdir.makedir('lycia')
        replace.in_environ('CLAUDECODE', '1')
        calls = _stub(replace)
        with ShouldRaise(
            UserError(
                '--dangerously-skip-permissions: not available when chimera is driven '
                'by an AI agent'
            )
        ):
            chat(ws, 'pegasus', extra=['--dangerously-skip-permissions'])
        compare(calls, expected=[])  # never launched

    def test_background_prompt_and_context(self, tmpdir: TempDir, replace: Replacer) -> None:
        ws = tmpdir.makedir('lycia')
        calls = _stub(replace)
        chat(ws, 'pegasus', 'plan the week', context=ws / 'ctx.md')
        expected = [
            'claude',
            '--bg',
            '--name',
            'pegasus',
            '--append-system-prompt-file',
            str(ws / 'ctx.md'),
            'plan the week',
        ]
        compare(calls, expected=[(expected, ws)])


def _workspace(tmpdir: TempDir, replace: Replacer, config: dict[str, object]) -> Path:
    ws = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', config)
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    os.chdir(ws)
    return ws


def test_chat_cli_launches_the_captain(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _workspace(
        tmpdir, replace, {'kind': 'workspace', 'captain': {'name': 'pegasus', 'model': 'opus'}}
    )
    directive = tmpdir.write(ws / 'roles' / 'captain' / 'directives.md', 'Direct the work.\n')
    calls = _stub(replace)
    run = command.run('chat')
    text = f'{CAPTAIN_TEXT}\n\n<!-- {directive.resolve()} (workspace) -->\nDirect the work.'
    digest = sha256(text.encode()).hexdigest()
    context = ws / 'state' / 'context' / f'@@captain-{digest[:8]}.md'
    compare(context.read_text(), expected=text)
    sources = context_sources(ws, 'captain')
    sources[str(ws / 'roles' / 'captain' / '*.md')] = [str(directive)]
    claude_cmd = [
        'claude',
        '--session-id',
        SESSION_ID,
        '--name',
        '@@captain',
        '--model',
        'opus',
        '--append-system-prompt-file',
        str(context),
    ]
    run.check(
        output=f'Launched chat @@captain in {ws}',
        logging=[
            {
                'level': 'INFO',
                'command': 'chat',
                'phase': 'start',
                'function': 'chimera.commands.chat.chat',
                'params': {
                    'prompt': None,
                    'resume': False,
                    'dangerous': False,
                    'harness': None,
                    'model': None,
                    'dry': False,
                    'project': None,
                    'goal': None,
                },
            },
            {
                'level': 'INFO',
                'session': '@@captain',
                'path': str(context),
                'sha256': digest,
                'sources': sources,
                'message': 'context: rendered',
            },
            launching(claude_cmd, ws),
            launched(claude_cmd, ws),
            {'level': 'INFO', 'command': 'chat', 'phase': 'end'},
        ],
    )
    compare(calls, expected=[(claude_cmd, ws)])


def test_chat_cli_project_scope(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    ws = _workspace(tmpdir, replace, {'kind': 'workspace', 'captain': 'pegasus'})
    project = ws / 'proj'
    project.mkdir()
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(project)})
    os.chdir(project)
    project = Path.cwd()  # resolves symlinks like the wrapper
    calls = _stub(replace)
    # role directives lead every chimera-launched session's context — the manager's too
    digest = sha256(MANAGER_TEXT.encode()).hexdigest()
    context = ws / 'state' / 'context' / f'proj@@manager-{digest[:8]}.md'
    claude_cmd = [
        'claude',
        '--session-id',
        SESSION_ID,
        '--name',
        'proj@@manager',
        '--append-system-prompt-file',
        str(context),
    ]
    command.run('chat').check(
        output=f'Launched chat proj@@manager in {project}',
        logging=[
            {
                'level': 'INFO',
                'command': 'chat',
                'phase': 'start',
                'function': 'chimera.commands.chat.chat',
                'params': {
                    'prompt': None,
                    'resume': False,
                    'dangerous': False,
                    'harness': None,
                    'model': None,
                    'dry': False,
                    'project': None,
                    'goal': None,
                },
            },
            {
                'level': 'INFO',
                'session': 'proj@@manager',
                'path': str(context),
                'sha256': digest,
                'sources': context_sources(ws, 'manager', pinned=project),
                'message': 'context: rendered',
            },
            launching(claude_cmd, project),
            launched(claude_cmd, project),
            {'level': 'INFO', 'command': 'chat', 'phase': 'end'},
        ],
    )
    compare(context.read_text(), expected=MANAGER_TEXT)
    compare(calls, expected=[(claude_cmd, project)])


def test_chat_cli_manager_layers_project_role_directives(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _workspace(tmpdir, replace, {'kind': 'workspace', 'captain': 'pegasus'})
    generic = tmpdir.write(ws / 'roles' / 'manager' / 'all.md', 'Keep goals moving.\n')
    project = ws / 'proj'
    persona = tmpdir.write(
        project / 'roles' / 'manager' / 'own.md', 'Watch the datacenter feeds.\n'
    )
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(project)})
    os.chdir(project)
    _stub(replace)
    # workspace directives (every manager) lead, the project's own persona follows
    text = (
        f'{MANAGER_TEXT}\n\n'
        f'<!-- {generic.resolve()} (workspace) -->\nKeep goals moving.\n\n'
        f'<!-- {persona.resolve()} (project) -->\nWatch the datacenter feeds.'
    )
    digest = sha256(text.encode()).hexdigest()
    context = ws / 'state' / 'context' / f'proj@@manager-{digest[:8]}.md'
    sources = context_sources(ws, 'manager', pinned=Path.cwd())
    sources[str(ws / 'roles' / 'manager' / '*.md')] = [str(generic)]
    sources[str(Path.cwd() / 'roles' / 'manager' / '*.md')] = [str(persona.resolve())]
    manager_cmd = [
        'claude',
        '--session-id',
        SESSION_ID,
        '--name',
        'proj@@manager',
        '--append-system-prompt-file',
        str(context),
    ]
    command.run('chat').check(
        output=f'Launched chat proj@@manager in {Path.cwd()}',
        logging=[
            {
                'level': 'INFO',
                'command': 'chat',
                'phase': 'start',
                'function': 'chimera.commands.chat.chat',
                'params': {
                    'prompt': None,
                    'resume': False,
                    'dangerous': False,
                    'harness': None,
                    'model': None,
                    'dry': False,
                    'project': None,
                    'goal': None,
                },
            },
            {
                'level': 'INFO',
                'session': 'proj@@manager',
                'path': str(context),
                'sha256': digest,
                'sources': sources,
                'message': 'context: rendered',
            },
            launching(manager_cmd, Path.cwd()),
            launched(manager_cmd, Path.cwd()),
            {'level': 'INFO', 'command': 'chat', 'phase': 'end'},
        ],
    )
    compare(context.read_text(), expected=text)


def test_chat_cli_goal_scope_refuses(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    ws = _workspace(tmpdir, replace, {'kind': 'workspace'})
    project = ws / 'proj'
    worktree = project / 'worktrees' / 'g@agent'
    worktree.mkdir(parents=True)
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(project)})
    os.chdir(worktree)
    calls = _stub(replace)
    message = (
        'a goal has its agent — ch agent resume -g g talks to it; '
        'ch chat from the proj dir for a side conversation'
    )
    command.run('chat').check(
        output=f'Error: {message}',
        return_code=1,
        logging=action_logs(
            'chat',
            'chimera.commands.chat.chat',
            {
                'prompt': None,
                'resume': False,
                'dangerous': False,
                'harness': None,
                'model': None,
                'dry': False,
                'project': None,
                'goal': None,
            },
            error=f'GoalHasAgentError: {message}',
        ),
    )
    compare(calls, expected=[])  # never launched


def test_chat_cli_explicit_goal_never_launches_the_captain(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _workspace(tmpdir, replace, {'kind': 'workspace', 'captain': 'pegasus'})
    calls = _stub(replace)
    message = (
        'a goal has its agent — ch agent resume -g ghost talks to it; '
        'ch chat from the <project> dir for a side conversation'
    )
    command.run('chat', '-g', 'ghost').check(
        output=f'Error: {message}',
        return_code=1,
        logging=action_logs(
            'chat',
            'chimera.commands.chat.chat',
            {
                'prompt': None,
                'resume': False,
                'dangerous': False,
                'harness': None,
                'model': None,
                'dry': False,
                'project': None,
                'goal': 'ghost',
            },
            error=f'GoalHasAgentError: {message}',
        ),
    )
    compare(calls, expected=[])  # never launched


def test_chat_cli_resume(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    ws = _workspace(tmpdir, replace, {'kind': 'workspace', 'captain': 'pegasus'})
    calls = _stub(replace)
    run = command.run('chat', '--resume')
    # even without a roles/captain dir the prime renders, so context is injected
    text = CAPTAIN_TEXT
    digest = sha256(text.encode()).hexdigest()
    context = ws / 'state' / 'context' / f'@@captain-{digest[:8]}.md'
    compare(context.read_text(), expected=text)
    claude_cmd = ['claude', '--resume', '@@captain', '--append-system-prompt-file', str(context)]
    run.check(
        output=f'Resumed chat @@captain in {ws}',
        logging=[
            {
                'level': 'INFO',
                'command': 'chat',
                'phase': 'start',
                'function': 'chimera.commands.chat.chat',
                'params': {
                    'prompt': None,
                    'resume': True,
                    'dangerous': False,
                    'harness': None,
                    'model': None,
                    'dry': False,
                    'project': None,
                    'goal': None,
                },
            },
            {
                'level': 'INFO',
                'session': '@@captain',
                'path': str(context),
                'sha256': digest,
                'sources': context_sources(ws, 'captain'),
                'message': 'context: rendered',
            },
            launching(claude_cmd, ws),
            launched(claude_cmd, ws),
            {'level': 'INFO', 'command': 'chat', 'phase': 'end'},
        ],
    )
    compare(calls, expected=[(claude_cmd, ws)])


def test_chat_cli_dry_previews_without_launching(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _workspace(tmpdir, replace, {'kind': 'workspace', 'captain': 'pegasus'})
    calls = _stub(replace)
    text = CAPTAIN_TEXT
    digest = sha256(text.encode()).hexdigest()
    context = ws / 'state' / 'context' / f'@@captain-{digest[:8]}.md'
    command.run('chat', '--dry').check(
        output='\n'.join(
            [
                f'Would launch chat @@captain in {ws}',
                'harness: claude',
                'role: captain',
                'prompt: (interactive)',
                *sources_lines(context_sources(ws, 'captain')),
                f'context: {context}',
                '---',
                text,
            ]
        ),
        logging=[
            {
                'level': 'INFO',
                'command': 'chat',
                'phase': 'start',
                'function': 'chimera.commands.chat.chat',
                'params': {
                    'prompt': None,
                    'resume': False,
                    'dangerous': False,
                    'harness': None,
                    'model': None,
                    'dry': True,
                    'project': None,
                    'goal': None,
                },
            },
            {
                'level': 'INFO',
                'session': '@@captain',
                'path': str(context),
                'sha256': digest,
                'sources': context_sources(ws, 'captain'),
                'message': 'context: rendered',
            },
            {'level': 'INFO', 'command': 'chat', 'phase': 'end'},
        ],
    )
    compare(calls, expected=[])  # nothing launched


def test_chat_cli_dry_previews_beside_the_live_chat(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _workspace(tmpdir, replace, {'kind': 'workspace', 'captain': 'pegasus'})
    calls = _stub(replace, live=[AgentSession('x', str(Captain()), 'idle', ws, None)])
    text = CAPTAIN_TEXT
    digest = sha256(text.encode()).hexdigest()
    context = ws / 'state' / 'context' / f'@@captain-{digest[:8]}.md'
    command.run('chat', '--dry').check(
        output='\n'.join(
            [
                f'Would launch chat @@captain in {ws}',
                "note: chat '@@captain' is already live — a real launch would refuse",
                'harness: claude',
                'role: captain',
                'prompt: (interactive)',
                *sources_lines(context_sources(ws, 'captain')),
                f'context: {context}',
                '---',
                text,
            ]
        ),
        logging=[
            {
                'level': 'INFO',
                'command': 'chat',
                'phase': 'start',
                'function': 'chimera.commands.chat.chat',
                'params': {
                    'prompt': None,
                    'resume': False,
                    'dangerous': False,
                    'harness': None,
                    'model': None,
                    'dry': True,
                    'project': None,
                    'goal': None,
                },
            },
            {
                'level': 'INFO',
                'session': '@@captain',
                'path': str(context),
                'sha256': digest,
                'sources': context_sources(ws, 'captain'),
                'message': 'context: rendered',
            },
            {'level': 'WARNING', 'session': '@@captain', 'message': 'chat: already live'},
            {'level': 'INFO', 'command': 'chat', 'phase': 'end'},
        ],
    )
    compare(calls, expected=[])  # nothing launched


def test_chat_cli_dry_project_scope_leads_with_the_manager_role(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _workspace(tmpdir, replace, {'kind': 'workspace', 'captain': 'pegasus'})
    project = ws / 'proj'
    project.mkdir()
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(project)})
    os.chdir(project)
    project = Path.cwd()  # resolves symlinks like the wrapper
    calls = _stub(replace)
    digest = sha256(MANAGER_TEXT.encode()).hexdigest()
    context = ws / 'state' / 'context' / f'proj@@manager-{digest[:8]}.md'
    command.run('chat', '--dry').check(
        output='\n'.join(
            [
                f'Would launch chat proj@@manager in {project}',
                'harness: claude',
                'role: manager (scope: proj)',
                'prompt: (interactive)',
                *sources_lines(context_sources(ws, 'manager', pinned=project)),
                f'context: {context}',
                '---',
                MANAGER_TEXT,
            ]
        ),
        logging=[
            {
                'level': 'INFO',
                'command': 'chat',
                'phase': 'start',
                'function': 'chimera.commands.chat.chat',
                'params': {
                    'prompt': None,
                    'resume': False,
                    'dangerous': False,
                    'harness': None,
                    'model': None,
                    'dry': True,
                    'project': None,
                    'goal': None,
                },
            },
            {
                'level': 'INFO',
                'session': 'proj@@manager',
                'path': str(context),
                'sha256': digest,
                'sources': context_sources(ws, 'manager', pinned=project),
                'message': 'context: rendered',
            },
            {'level': 'INFO', 'command': 'chat', 'phase': 'end'},
        ],
    )
    compare(calls, expected=[])  # nothing launched


def test_chat_env_overlay_reaches_the_adapter(tmpdir: TempDir, replace: Replacer) -> None:
    ws = tmpdir.makedir('lycia')
    _stub(replace)  # keeps the by-name guard inert
    envs = capture_env(replace)
    chat(ws, 'pegasus', env={'CHIMERA_ROLE': 'captain'})
    chat(ws, 'pegasus', env={'CHIMERA_ROLE': 'captain'}, resume=True)
    compare(envs, expected=[{'CHIMERA_ROLE': 'captain'}, {'CHIMERA_ROLE': 'captain'}])


def test_chat_cli_stamps_the_captain_role(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    _workspace(tmpdir, replace, {'kind': 'workspace', 'captain': 'pegasus'})
    _stub(replace)
    envs = capture_env(replace)
    command.run('chat')
    # no scope: the captain is unfenced — stamped '' so nothing inherited can fence it
    compare(envs, expected=[{'CHIMERA_ROLE': 'captain', 'CHIMERA_ROLE_SCOPE': ''}])


def test_chat_cli_stamps_the_manager_role_fenced_to_the_project(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _workspace(tmpdir, replace, {'kind': 'workspace', 'captain': 'pegasus'})
    project = ws / 'proj'
    project.mkdir()
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(project)})
    os.chdir(project)
    _stub(replace)
    envs = capture_env(replace)
    command.run('chat')
    compare(envs, expected=[{'CHIMERA_ROLE': 'manager', 'CHIMERA_ROLE_SCOPE': 'proj'}])
