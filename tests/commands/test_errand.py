import os
from collections.abc import Callable, Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from secrets import token_hex

import pytest
from giterator.testing import Repo
from testfixtures import Replacer, ShouldRaise, TempDir, compare

import chimera.__main__ as main
import chimera.commands.errand as errand_mod
from chimera.agents.claude import Claude
from chimera.agents.registry import AgentSpec
from chimera.commands.agent import live
from chimera.commands.errand import GUARDRAIL, ErrandResult, _fresh_goal, errand
from chimera.commands.worktree import rm as worktree_rm
from chimera.commands.worktree.rm import remove
from chimera.config import UserError
from chimera.dry import Dry
from chimera.git import Git
from tests.cli import Command, action_logs, context_sources, general_capture, sources_lines

REFUSED_BYPASS = (
    '--dangerously-skip-permissions: not available when chimera is driven by an AI agent'
)


@pytest.fixture(autouse=True)
def _no_agents(replace: Replacer) -> None:
    # the sweep's liveness guard must never consult a real claude — hermetic with or
    # without the binary installed (the same pattern as tests/commands/project/test_rm.py)
    replace.in_module(live, lambda worktree: [], module=worktree_rm)


def _stub_run(
    replace: Replacer,
    report: str = 'the report',
    raises: Exception | None = None,
    dirty: bool = False,
) -> list[dict[str, object]]:
    """Stub the headless harness run, capturing every argument it was handed."""
    calls: list[dict[str, object]] = []

    def fake_run(
        self: Claude,
        cwd: Path,
        name: str,
        prompt: str,
        extra: Sequence[str] = (),
        *,
        model: str | None = None,
        context: Path | None = None,
        env: Mapping[str, str] = {},
        readonly: bool = True,
        timeout: float | None = None,
    ) -> str:
        calls.append(
            {
                'cwd': cwd,
                'name': name,
                'prompt': prompt,
                'extra': tuple(extra),
                'model': model,
                'context': context,
                'env': env,
                'readonly': readonly,
                'timeout': timeout,
            }
        )
        if dirty:
            (cwd / 'scratch.md').write_text('working notes\n')
        if raises is not None:
            raise raises
        return report

    replace.on_class(Claude.run, fake_run)
    return calls


class TestFreshGoal:
    def test_shape(self, tmpdir: TempDir) -> None:
        goal = _fresh_goal(tmpdir / 'wt')
        prefix, suffix = goal.split('-')
        compare(prefix, expected='errand')
        compare(len(suffix), expected=6)
        int(suffix, 16)  # hex by construction — so always a valid goal name

    def test_collision_retries(self, tmpdir: TempDir, replace: Replacer) -> None:
        (tmpdir / 'wt' / 'errand-aaaaaa@agent').mkdir(parents=True)
        tokens = iter(['aaaaaa', 'bbbbbb'])
        replace.in_module(token_hex, lambda n: next(tokens), module=errand_mod)
        compare(_fresh_goal(tmpdir / 'wt'), expected='errand-bbbbbb')


class TestErrand:
    def test_runs_readonly_on_the_guardrailed_prompt_and_sweeps_the_goal(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer
    ) -> None:
        calls = _stub_run(replace)
        result = errand(git_repo.path, tmpdir / 'wt', 'proj', 'what is X?')
        goal = result.goal
        compare(
            result,
            expected=ErrandResult(
                goal=goal,
                worktree=tmpdir / 'wt' / f'{goal}@agent',
                out=None,
                report='the report',
                cleaned=True,
            ),
        )
        compare(
            calls,
            expected=[
                {
                    'cwd': tmpdir / 'wt' / f'{goal}@agent',
                    'name': f'proj@{goal}@agent',
                    'prompt': GUARDRAIL + 'what is X?',
                    'extra': (),
                    'model': None,
                    'context': None,
                    'env': {},
                    'readonly': True,
                    'timeout': None,
                }
            ],
        )
        # the ephemeral goal is gone again: worktree and branch both swept
        tmpdir.compare(path='wt', expected=())
        compare(Git(git_repo.path).branches(), expected=['main'])

    def test_factories_key_by_the_resolved_session_name(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer
    ) -> None:
        calls = _stub_run(replace)
        names: list[str] = []

        def context(name: str) -> Path:
            names.append(name)
            return tmpdir / 'ctx.md'

        result = errand(
            git_repo.path,
            tmpdir / 'wt',
            'proj',
            'q',
            extra=('--verbose',),
            timeout=30,
            spec=AgentSpec(model='opus'),
            context=context,
            env=lambda name: {'CHIMERA_ROLE': 'agent'},
        )
        compare(names, expected=[f'proj@{result.goal}@agent'])
        compare(
            calls,
            expected=[
                {
                    'cwd': result.worktree,
                    'name': f'proj@{result.goal}@agent',
                    'prompt': GUARDRAIL + 'q',
                    'extra': ('--verbose',),
                    'model': 'opus',
                    'context': tmpdir / 'ctx.md',
                    'env': {'CHIMERA_ROLE': 'agent'},
                    'readonly': True,
                    'timeout': 30,
                }
            ],
        )

    def test_out_write_is_logged_with_its_hash(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer
    ) -> None:
        _stub_run(replace, report='# Findings\n')
        out = tmpdir / 'report.md'
        with general_capture() as log:
            result = errand(git_repo.path, tmpdir / 'wt', 'proj', 'q', out=out)
        compare(out.read_text(), expected='# Findings\n')
        goal, head = result.goal, Git(git_repo.path).rev_parse('main', short=False)
        log.check(
            {
                'level': 'INFO',
                'message': 'errand: refs',
                'goal': goal,
                'worktree': str(result.worktree),
                'git': {'before': {}, 'after': {f'{goal}/agent': head}},
            },
            {
                'level': 'INFO',
                'message': 'errand: result',
                'session': f'proj@{goal}@agent',
                'path': str(out),
                'bytes': 11,
                'sha256': sha256(b'# Findings\n').hexdigest(),
            },
            {
                'level': 'INFO',
                'message': 'worktree rm: refs',
                'goal': goal,
                'force': False,
                'git': {'before': {f'{goal}/agent': head}, 'after': {}},
            },
        )

    def test_stdout_mode_delivers_via_the_result_alone(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer
    ) -> None:
        _stub_run(replace)
        with general_capture() as log:
            result = errand(git_repo.path, tmpdir / 'wt', 'proj', 'q')
        assert result.out is None
        compare(result.report, expected='the report')
        # no errand: result line — nothing was written anywhere
        compare(
            [entry['message'] for entry in log.actual()],
            expected=['errand: refs', 'worktree rm: refs'],
        )

    def test_dirty_worktree_is_reported_not_failed(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer
    ) -> None:
        _stub_run(replace, dirty=True)
        with general_capture() as log:
            result = errand(git_repo.path, tmpdir / 'wt', 'proj', 'q')
        compare(result.report, expected='the report')  # the errand itself succeeded
        assert not result.cleaned
        assert result.worktree.is_dir()  # left standing for inspection
        assert f'{result.goal}/agent' in Git(git_repo.path).branches()
        refusal = (
            f'refusing to clean up (use --force to discard):\n'
            f'  {result.worktree} has uncommitted or untracked changes'
        )
        log.check_present(
            {
                'level': 'WARNING',
                'message': 'errand: cleanup refused',
                'goal': result.goal,
                'refusal': refusal,
            }
        )

    def test_keep_leaves_the_goal_standing(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer
    ) -> None:
        _stub_run(replace)
        result = errand(git_repo.path, tmpdir / 'wt', 'proj', 'q', keep=True)
        assert not result.cleaned
        assert result.worktree.is_dir()
        assert f'{result.goal}/agent' in Git(git_repo.path).branches()

    def test_run_failure_still_sweeps_then_raises(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer
    ) -> None:
        _stub_run(replace, raises=RuntimeError('claude exploded'))
        with ShouldRaise(RuntimeError('claude exploded')):
            errand(git_repo.path, tmpdir / 'wt', 'proj', 'q')
        tmpdir.compare(path='wt', expected=())
        compare(Git(git_repo.path).branches(), expected=['main'])

    def test_run_failure_sweep_failure_never_displaces_the_run_error(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer
    ) -> None:
        _stub_run(replace, raises=RuntimeError('claude exploded'))

        def broken_remove(*args: object, **kw: object) -> None:
            raise OSError('rmtree died')  # not the refusal type _finish handles

        replace.in_module(remove, broken_remove, module=errand_mod)
        with general_capture() as log:
            with ShouldRaise(RuntimeError('claude exploded')):
                errand(git_repo.path, tmpdir / 'wt', 'proj', 'q')
        [worktree] = (tmpdir / 'wt').iterdir()  # the failed sweep left the goal standing
        goal = worktree.name.removesuffix('@agent')
        log.check_present(
            {
                'level': 'WARNING',
                'message': 'errand: cleanup failed',
                'goal': goal,
                'worktree': str(worktree),
                'error': 'rmtree died',
            }
        )

    def test_run_failure_with_keep_leaves_the_goal(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer
    ) -> None:
        _stub_run(replace, raises=RuntimeError('boom'))
        with ShouldRaise(RuntimeError('boom')):
            errand(git_repo.path, tmpdir / 'wt', 'proj', 'q', keep=True)
        [worktree] = (tmpdir / 'wt').iterdir()  # kept for the post-mortem

    def test_reference_project_refuses_up_front(self, tmpdir: TempDir) -> None:
        missing = tmpdir / 'proj' / 'repo'
        with ShouldRaise(
            UserError(f"project 'proj' has no repo checkout at {missing} to dispatch into")
        ):
            errand(missing, tmpdir / 'wt', 'proj', 'q')

    def test_bypass_passthrough_refused_under_an_ai_agent(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer
    ) -> None:
        replace.in_environ('CLAUDECODE', '1')
        calls = _stub_run(replace)
        with ShouldRaise(UserError(REFUSED_BYPASS)):
            errand(
                git_repo.path,
                tmpdir / 'wt',
                'proj',
                'q',
                extra=['--dangerously-skip-permissions'],
            )
        compare(calls, expected=[])  # never ran…
        compare(Git(git_repo.path).branches(), expected=['main'])  # …and nothing was set up

    def test_dry_resolves_everything_but_mutates_nothing(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer
    ) -> None:
        calls = _stub_run(replace)
        with general_capture() as log:
            result = errand(
                git_repo.path, tmpdir / 'wt', 'proj', 'q', out=tmpdir / 'r.md', dry=Dry(True)
            )
        assert result.goal.startswith('errand-')  # the id resolved for real
        compare(result.report, expected='')
        assert result.cleaned  # the sweep would find nothing to refuse over
        compare(calls, expected=[])  # the harness never ran
        assert not (tmpdir / 'wt').exists()  # no worktree tree
        assert not (tmpdir / 'r.md').exists()  # no report file
        compare(Git(git_repo.path).branches(), expected=['main'])  # no branch
        log.check()  # no refs changed, so no ref lines either


def _workspace_project(tmpdir: TempDir, git_repo: Repo, replace: Replacer) -> Path:
    """A workspace holding one project 'proj' backed by ``git_repo``; cwd at its root."""
    ws = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    os.chdir(ws)
    return ws


def _stub_errand(
    replace: Replacer, cleaned: bool = True, call_context: bool = False
) -> list[dict[str, object]]:
    """Stub the pure function behind the CLI, mirroring how the real one uses the factories:
    the role stamp is always resolved on launch; the context render only when asked for."""
    calls: list[dict[str, object]] = []

    def record(
        repo: Path,
        worktrees_root: Path,
        target: str,
        prompt: str,
        out: Path | None = None,
        extra: Sequence[str] = (),
        keep: bool = False,
        frm: str | None = None,
        fetch: bool = True,
        timeout: float | None = None,
        spec: AgentSpec = AgentSpec(),
        context: Callable[[str], Path | None] | None = None,
        env: Callable[[str], Mapping[str, str]] | None = None,
        dry: Dry = Dry(),
    ) -> ErrandResult:
        name = f'{target}@errand-abc123@agent'
        rendered = context(name) if call_context and context is not None else None
        stamp = env(name) if env is not None else None
        calls.append(
            {
                'repo': repo,
                'target': target,
                'prompt': prompt,
                'out': out,
                'extra': list(extra),
                'keep': keep,
                'frm': frm,
                'fetch': fetch,
                'timeout': timeout,
                'spec': spec,
                'rendered': rendered,
                'stamp': stamp,
                'dry': dry,
            }
        )
        return ErrandResult(
            'errand-abc123', worktrees_root / 'errand-abc123@agent', out, 'the report', cleaned
        )

    replace(target=errand, container=main, name='_errand', replacement=record)
    return calls


def _params(**overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        'target': 'proj',
        'prompt': 'q',
        'out': None,
        'keep': False,
        'timeout': None,
        'frm': None,
        'offline': False,
        'harness': None,
        'model': None,
        'dry': False,
    }
    params.update(overrides)
    return params


class TestErrandCli:
    def test_prints_the_report(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
    ) -> None:
        _workspace_project(tmpdir, git_repo, replace)
        calls = _stub_errand(replace)
        command.run('errand', 'proj', 'q').check(
            output='the report',
            logging=action_logs('errand', 'chimera.commands.errand.errand', _params()),
        )
        [call] = calls
        compare(call['repo'], expected=git_repo.path)
        compare(
            call['stamp'],
            expected={'CHIMERA_ROLE': 'agent', 'CHIMERA_ROLE_SCOPE': 'proj@errand-abc123'},
        )

    def test_renders_the_target_projects_context(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
    ) -> None:
        _workspace_project(tmpdir, git_repo, replace)
        calls = _stub_errand(replace, call_context=True)
        compare(command.run('errand', 'proj', 'q').return_code, expected=0)
        [call] = calls
        rendered = call['rendered']
        assert isinstance(rendered, Path)
        assert rendered.name.startswith('proj@errand-abc123@agent-')
        assert (
            'You are the agent for goal errand-abc123 on proj; '
            'this worktree and branch are your entire workspace.'
        ) in rendered.read_text()
        # the bare identity sentence, never the agent prime: commit-as-you-go would
        # contradict the errand's read-only wall
        assert 'committing as you go' not in rendered.read_text()

    def test_out_reports_the_write(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
    ) -> None:
        _workspace_project(tmpdir, git_repo, replace)
        calls = _stub_errand(replace)
        command.run('errand', 'proj', 'q', '--out', 'r.md').check(
            output='Wrote report to r.md',
            # the logged param is the raw parse; typer converts to Path only in the callback
            logging=action_logs('errand', 'chimera.commands.errand.errand', _params(out='r.md')),
        )
        compare(calls[0]['out'], expected=Path('r.md'))

    def test_keep_reports_the_kept_worktree(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
    ) -> None:
        ws = _workspace_project(tmpdir, git_repo, replace)
        _stub_errand(replace, cleaned=False)
        worktree = ws / 'proj' / 'worktrees' / 'errand-abc123@agent'
        command.run('errand', 'proj', 'q', '--keep').check(
            output=f'the report\nKept {worktree} — ch goal finish errand-abc123 -p proj cleans up',
            logging=action_logs('errand', 'chimera.commands.errand.errand', _params(keep=True)),
        )

    def test_cleanup_refusal_is_reported_without_failing(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
    ) -> None:
        ws = _workspace_project(tmpdir, git_repo, replace)
        _stub_errand(replace, cleaned=False)
        worktree = ws / 'proj' / 'worktrees' / 'errand-abc123@agent'
        command.run('errand', 'proj', 'q').check(
            output=f'the report\n'
            f'errand left work in {worktree} — inspect it; '
            f'ch goal finish errand-abc123 -p proj cleans up',
            logging=action_logs('errand', 'chimera.commands.errand.errand', _params()),
        )

    def test_forwards_the_passthrough_tail(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
    ) -> None:
        _workspace_project(tmpdir, git_repo, replace)
        calls = _stub_errand(replace)
        command.run('errand', 'proj', 'q', '--', '--model', 'opus').check(
            output='the report',
            logging=action_logs('errand', 'chimera.commands.errand.errand', _params()),
        )
        compare(calls[0]['extra'], expected=['--model', 'opus'])

    def test_inherited_p_is_refused(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
    ) -> None:
        _workspace_project(tmpdir, git_repo, replace)
        calls = _stub_errand(replace)
        message = 'errand names its target positionally — drop -p'
        command.run('-p', 'proj', 'errand', 'proj', 'q').check(
            output=f'Error: {message}',
            return_code=1,
            logging=action_logs(
                'errand',
                'chimera.commands.errand.errand',
                _params(),
                error=f'UserError: {message}',
            ),
        )
        compare(calls, expected=[])

    def test_bypass_passthrough_refused_under_claudecode(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
    ) -> None:
        _workspace_project(tmpdir, git_repo, replace)
        replace.in_environ('CLAUDECODE', '1')
        command.run('errand', 'proj', 'q', '--', '--dangerously-skip-permissions').check(
            output=f'Error: {REFUSED_BYPASS}',
            return_code=1,
            logging=action_logs(
                'errand',
                'chimera.commands.errand.errand',
                _params(),
                error=f'UserError: {REFUSED_BYPASS}',
            ),
        )

    def test_dry_previews_target_out_and_context(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
    ) -> None:
        ws = _workspace_project(tmpdir, git_repo, replace)
        _stub_errand(replace, call_context=True)
        worktree = ws / 'proj' / 'worktrees' / 'errand-abc123@agent'
        text = (
            '# Role: agent\n\nYou are the agent for goal errand-abc123 on proj; '
            'this worktree and branch are your entire workspace.'
        )
        digest = sha256(text.encode()).hexdigest()
        artifact = ws / 'logs' / 'context' / f'proj@errand-abc123@agent-{digest[:8]}.md'
        sources = context_sources(ws, 'agent', pinned=ws / 'proj')
        start, end = action_logs('errand', 'chimera.commands.errand.errand', _params(dry=True))
        command.run('errand', 'proj', 'q', '--dry').check(
            output='\n'.join(
                [
                    f'Would run errand errand-abc123 in {worktree}',
                    'target: proj',
                    'out: (stdout)',
                    'harness: claude',
                    'role: agent (scope: proj@errand-abc123)',
                    'prompt: q (guardrail prepended)',
                    *sources_lines(sources),
                    f'context: {artifact}',
                    '---',
                    text,
                ]
            ),
            logging=[
                start,
                {
                    'level': 'INFO',
                    'message': 'context: rendered',
                    'session': 'proj@errand-abc123@agent',
                    'path': str(artifact),
                    'sha256': digest,
                    'sources': sources,
                },
                end,
            ],
        )

    def test_dry_names_the_out_path(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
    ) -> None:
        ws = _workspace_project(tmpdir, git_repo, replace)
        _stub_errand(replace)
        worktree = ws / 'proj' / 'worktrees' / 'errand-abc123@agent'
        command.run('errand', 'proj', 'q', '--dry', '--out', 'r.md').check(
            output='\n'.join(
                [
                    f'Would run errand errand-abc123 in {worktree}',
                    'target: proj',
                    'out: r.md',
                    'harness: claude',
                    'role: agent (scope: proj@errand-abc123)',
                    'prompt: q (guardrail prepended)',
                    'context: (none)',
                ]
            ),
            logging=action_logs(
                'errand',
                'chimera.commands.errand.errand',
                _params(dry=True, out='r.md'),
            ),
        )


def test_foreign_has_exactly_one_caller() -> None:
    # the fence-exempt axis must stay errand's alone — a second caller would quietly
    # widen the exemption into a general escape hatch (see _foreign's docstring)
    source = Path(str(main.__file__)).read_text()
    compare(source.count('_foreign('), expected=2)  # its def and the single call site


def _fenced(tmpdir: TempDir, git_repo: Repo, replace: Replacer, role: str, scope: str) -> Path:
    """Two projects, the session role-stamped and fenced to 'proj'; cwd inside it."""
    ws = _workspace_project(tmpdir, git_repo, replace)
    tmpdir.dump('lycia/other/config.yaml', {'kind': 'project', 'repo': str(git_repo.path)})
    replace.in_environ('CHIMERA_ROLE', role)
    replace.in_environ('CHIMERA_ROLE_SCOPE', scope)
    os.chdir(ws / 'proj')
    return ws


class TestErrandFence:
    """The target axis is unfenced while the "who I act as" axis stays fenced."""

    def _dispatches_but_cannot_act(self, replace: Replacer, command: Command) -> None:
        calls = _stub_errand(replace)
        # dispatching INTO the other project proceeds…
        compare(command.run('errand', 'other', 'q', '--dry').return_code, expected=0)
        [call] = calls
        compare(call['target'], expected='other')
        compare(call['dry'], expected=Dry(True))
        # …while acting AS it still refuses at the fence, from the very same session
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

    def test_manager_dispatches_across_the_fence(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
    ) -> None:
        _fenced(tmpdir, git_repo, replace, 'manager', 'proj')
        self._dispatches_but_cannot_act(replace, command)

    def test_agent_dispatches_across_the_fence(
        self, tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
    ) -> None:
        _fenced(tmpdir, git_repo, replace, 'agent', 'proj@g')
        self._dispatches_but_cannot_act(replace, command)
