import os
import subprocess
from hashlib import sha256
from collections.abc import Iterable
from pathlib import Path

from testfixtures import Replacer, ShouldRaise, TempDir, compare

from chimera.agents import Session
from chimera.agents.claude import Claude
from chimera.commands.chat import ChatAlreadyLiveError, NoGoalWorktreeError, chat, chat_target
from chimera.config import ProjectConfig, UserError
from chimera.context import Project, Scope
from tests.cli import Command, action_logs


def _project_obj(directory: Path) -> Project:
    return Project(directory, ProjectConfig(kind='project', repo=Path('/r')))


def _stub(replace: Replacer, live: Iterable[Session] = ()) -> list[object]:
    calls: list[object] = []
    real_run = subprocess.run

    def fake_run(cmd, *args, cwd=None, check=False, **kw):  # noqa: ANN001, ANN002, ANN003
        # scope resolution legitimately shells out to git — only claude launches are ours
        if cmd and cmd[0] == 'claude':
            return calls.append((cmd, cwd, check))
        return real_run(cmd, *args, cwd=cwd, check=check, **kw)

    replace.on_class(Claude.live, lambda self, cwd=None: list(live))
    replace.in_module(subprocess.run, fake_run)
    return calls


class TestChatTarget:
    def test_workspace_scope_is_the_captain(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('lycia')
        compare(chat_target(Scope(ws, None, None), 'pegasus'), expected=(ws, 'pegasus'))

    def test_project_scope(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('lycia')
        project = _project_obj(ws / 'proj')
        compare(
            chat_target(Scope(ws, project, None), 'pegasus'),
            expected=(ws / 'proj', 'proj@chat'),
        )

    def test_goal_scope_chats_in_the_agent_worktree(self, tmpdir: TempDir) -> None:
        worktree = tmpdir.makedir('lycia/proj/worktrees/g@agent')
        project = _project_obj(tmpdir / 'lycia' / 'proj')
        compare(
            chat_target(Scope(tmpdir / 'lycia', project, 'g'), 'pegasus'),
            expected=(worktree, 'proj@g@chat'),
        )

    def test_ghost_goal_refuses(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('lycia')
        project = _project_obj(ws / 'proj')
        with ShouldRaise(NoGoalWorktreeError('ghost')):
            chat_target(Scope(ws, project, 'ghost'), 'pegasus')


class TestChat:
    def test_launches_alongside_live_sessions(self, tmpdir: TempDir, replace: Replacer) -> None:
        ws = tmpdir.makedir('lycia')
        # another session is live in the same cwd — chat launches anyway (not exclusive)
        calls = _stub(replace, live=[Session('x', 'other', 'idle', ws, None)])
        chat(ws, 'pegasus')
        compare(calls, expected=[(['claude', '--name', 'pegasus'], ws, True)])

    def test_resume_revives_by_name(self, tmpdir: TempDir, replace: Replacer) -> None:
        ws = tmpdir.makedir('lycia')
        calls = _stub(replace)
        chat(ws, 'pegasus', resume=True)
        compare(calls, expected=[(['claude', '--resume', 'pegasus'], ws, True)])

    def test_refuses_when_the_chat_itself_is_live(self, tmpdir: TempDir, replace: Replacer) -> None:
        ws = tmpdir.makedir('lycia')
        calls = _stub(replace, live=[Session('x', 'pegasus', 'idle', ws, None)])
        with ShouldRaise(ChatAlreadyLiveError('pegasus')):
            chat(ws, 'pegasus')
        with ShouldRaise(ChatAlreadyLiveError('pegasus')):  # resume can't attach either
            chat(ws, 'pegasus', resume=True)
        compare(calls, expected=[])  # never launched

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
        compare(calls, expected=[(expected, ws, True)])


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
    tmpdir.write(ws / 'roles' / 'captain' / 'directives.md', 'Direct the work.\n')
    calls = _stub(replace)
    run = command.run('chat')
    text = (
        '# Role: captain\n\n'
        'You are pegasus, the captain of the lycia workspace.\n\n'
        'Direct the work.'
    )
    digest = sha256(text.encode()).hexdigest()
    context = ws / 'logs' / 'context' / f'pegasus-{digest[:8]}.md'
    compare(context.read_text(), expected=text)
    run.check(
        output=f'Launched chat pegasus in {ws}',
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
                'session': 'pegasus',
                'path': str(context),
                'sha256': digest,
                'message': 'context: rendered',
            },
            {'level': 'INFO', 'command': 'chat', 'phase': 'end'},
        ],
    )
    claude_cmd = [
        'claude',
        '--name',
        'pegasus',
        '--model',
        'opus',
        '--append-system-prompt-file',
        str(context),
    ]
    compare(calls, expected=[(claude_cmd, ws, True)])


def test_chat_cli_project_scope(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    ws = _workspace(tmpdir, replace, {'kind': 'workspace', 'captain': 'pegasus'})
    project = ws / 'proj'
    project.mkdir()
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(project)})
    os.chdir(project)
    project = Path.cwd()  # resolves symlinks like the wrapper
    calls = _stub(replace)
    command.run('chat').check(
        output=f'Launched chat proj@chat in {project}',
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
        ),
    )
    compare(calls, expected=[(['claude', '--name', 'proj@chat'], project, True)])


def test_chat_cli_goal_scope_from_worktree(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _workspace(tmpdir, replace, {'kind': 'workspace'})
    project = ws / 'proj'
    worktree = project / 'worktrees' / 'g@agent'
    worktree.mkdir(parents=True)
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(project)})
    os.chdir(worktree)
    worktree = Path.cwd()  # resolves symlinks like the wrapper
    calls = _stub(replace)
    command.run('chat').check(
        output=f'Launched chat proj@g@chat in {worktree}',
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
        ),
    )
    compare(calls, expected=[(['claude', '--name', 'proj@g@chat'], worktree, True)])


def test_chat_cli_ghost_goal_errors(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    ws = _workspace(tmpdir, replace, {'kind': 'workspace', 'captain': 'pegasus'})
    project = ws / 'proj'
    project.mkdir()
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(project)})
    os.chdir(project)
    calls = _stub(replace)
    message = "goal 'ghost' has no agent worktree — ch goal start ghost creates it"
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
            error=f'NoGoalWorktreeError: {message}',
        ),
    )
    compare(calls, expected=[])  # never launched


def test_chat_cli_resume(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    ws = _workspace(tmpdir, replace, {'kind': 'workspace', 'captain': 'pegasus'})
    calls = _stub(replace)
    run = command.run('chat', '--resume')
    # even without a roles/captain dir the intro line renders, so context is injected
    text = '# Role: captain\n\nYou are pegasus, the captain of the lycia workspace.'
    digest = sha256(text.encode()).hexdigest()
    context = ws / 'logs' / 'context' / f'pegasus-{digest[:8]}.md'
    compare(context.read_text(), expected=text)
    run.check(
        output=f'Resumed chat pegasus in {ws}',
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
                'session': 'pegasus',
                'path': str(context),
                'sha256': digest,
                'message': 'context: rendered',
            },
            {'level': 'INFO', 'command': 'chat', 'phase': 'end'},
        ],
    )
    claude_cmd = ['claude', '--resume', 'pegasus', '--append-system-prompt-file', str(context)]
    compare(calls, expected=[(claude_cmd, ws, True)])


def test_chat_cli_dry_previews_without_launching(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    ws = _workspace(tmpdir, replace, {'kind': 'workspace', 'captain': 'pegasus'})
    calls = _stub(replace)
    text = '# Role: captain\n\nYou are pegasus, the captain of the lycia workspace.'
    digest = sha256(text.encode()).hexdigest()
    context = ws / 'logs' / 'context' / f'pegasus-{digest[:8]}.md'
    command.run('chat', '--dry').check(
        output='\n'.join(
            [
                f'Would launch chat pegasus in {ws}',
                'harness: claude',
                'prompt: (interactive)',
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
                'session': 'pegasus',
                'path': str(context),
                'sha256': digest,
                'message': 'context: rendered',
            },
            {'level': 'INFO', 'command': 'chat', 'phase': 'end'},
        ],
    )
    compare(calls, expected=[])  # nothing launched
