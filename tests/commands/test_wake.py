import os
from pathlib import Path

from testfixtures import Replacer, ShouldRaise, TempDir, compare

from chimera.agents import Session
from chimera.agents.claude import Claude
from chimera.commands.wake import wake
from chimera.config import UserError
from chimera.dry import Dry
from tests.cli import Command, action_logs


def _parked(cwd: Path) -> Session:
    return Session(
        'ab12cd34-e776-4059', 'p@g@agent', 'parked', cwd, None, kind='background', parked=True
    )


def _stub(replace: Replacer, sessions: list[Session]) -> list[tuple[Session, float]]:
    """Registry answers ``sessions`` everywhere; record what gets woken."""
    woken: list[tuple[Session, float]] = []
    replace.on_class(Claude.checked, lambda self, cwd=None: list(sessions), name='checked')
    replace.on_class(
        Claude.wake,
        lambda self, session, timeout=30.0: woken.append((session, timeout)),
        name='wake',
    )
    return woken


class TestWake:
    def test_goal_worktree_wins(self, tmpdir: TempDir, replace: Replacer) -> None:
        worktree = tmpdir.makedir('wt')
        session = _parked(worktree)
        woken = _stub(replace, [session])
        compare(wake(worktree, None), expected=session)
        compare(woken, expected=[(session, 30.0)])

    def test_dry_wakes_nothing(self, tmpdir: TempDir, replace: Replacer) -> None:
        worktree = tmpdir.makedir('wt')
        session = _parked(worktree)
        woken = _stub(replace, [session])
        compare(wake(worktree, None, dry=Dry(on=True)), expected=session)
        compare(woken, expected=[])

    def test_a_live_session_refuses_pointing_at_attach(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        worktree = tmpdir.makedir('wt')
        live = Session('ab12cd34-e776', 'p@g@agent', 'busy', worktree, None, pid=os.getpid())
        _stub(replace, [live])
        with ShouldRaise(
            UserError(f'nothing parked in {worktree} — already live: ab12cd34 (busy); attach to it')
        ):
            wake(worktree, None)

    def test_an_empty_worktree_points_at_resume(self, tmpdir: TempDir, replace: Replacer) -> None:
        worktree = tmpdir.makedir('wt')
        _stub(replace, [])
        with ShouldRaise(
            UserError(
                f'nothing parked in {worktree} — a dead session is revived with ch agent resume'
            )
        ):
            wake(worktree, None)

    def test_a_stale_corpse_also_points_at_resume(self, tmpdir: TempDir, replace: Replacer) -> None:
        worktree = tmpdir.makedir('wt')
        corpse = Session('x', 'p@g@agent', '?', worktree, None, stale='registry remnant')
        _stub(replace, [corpse])
        with ShouldRaise(
            UserError(
                f'nothing parked in {worktree} — a dead session is revived with ch agent resume'
            )
        ):
            wake(worktree, None)

    def test_target_names_a_parked_session(self, replace: Replacer) -> None:
        session = _parked(Path('/elsewhere'))
        woken = _stub(replace, [session])
        # by name, full id, or short id
        for target in ('p@g@agent', 'ab12cd34-e776-4059', 'ab12cd34'):
            compare(wake(None, target), expected=session)
        compare([s for s, _ in woken], expected=[session] * 3)

    def test_a_missing_worktree_falls_through_to_the_target(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        session = _parked(Path('/elsewhere'))
        _stub(replace, [session])
        compare(wake(tmpdir / 'nope', 'p@g@agent'), expected=session)

    def test_target_matching_nothing_refuses(self, replace: Replacer) -> None:
        _stub(replace, [_parked(Path('/elsewhere'))])
        with ShouldRaise(
            UserError('ghost: neither a goal with a worktree nor a parked session by that name/id')
        ):
            wake(None, 'ghost')

    def test_nothing_resolved_refuses(self) -> None:
        with ShouldRaise(
            UserError('nothing to wake: no goal worktree resolved and no session named')
        ):
            wake(None, None)


def _project_with_worktree(tmpdir: TempDir) -> Path:
    project = tmpdir.makedir('myproject')
    tmpdir.dump('myproject/config.yaml', {'kind': 'project', 'repo': str(project)})
    worktree = project / 'worktrees' / 'g@agent'
    worktree.mkdir(parents=True)
    os.chdir(project)
    return worktree


def test_wake_cli_by_goal(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    worktree = _project_with_worktree(tmpdir)
    woken = _stub(replace, [_parked(worktree)])
    command.run('wake', '-g', 'g').check(
        output=f'Woke p@g@agent (ab12cd34) in {worktree}',
        logging=action_logs(
            'wake',
            'chimera.commands.wake.wake',
            {'target': None, 'goal': 'g', 'actor': None, 'dry': False, 'project': None},
        ),
    )
    compare([s.name for s, _ in woken], expected=['p@g@agent'])


def test_wake_cli_dry_previews(tmpdir: TempDir, replace: Replacer, command: Command) -> None:
    worktree = _project_with_worktree(tmpdir)
    woken = _stub(replace, [_parked(worktree)])
    command.run('wake', '-g', 'g', '--dry').check(
        output=f'Would wake p@g@agent (ab12cd34) in {worktree}',
        logging=action_logs(
            'wake',
            'chimera.commands.wake.wake',
            {'target': None, 'goal': 'g', 'actor': None, 'dry': True, 'project': None},
        ),
    )
    compare(woken, expected=[])


def test_wake_cli_by_session_name_outside_any_project(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    os.chdir(tmpdir.path)  # nowhere near a project: only the target can name the session
    session = _parked(tmpdir / 'far')
    _stub(replace, [session])
    command.run('wake', 'p@g@agent').check(
        output=f'Woke p@g@agent (ab12cd34) in {tmpdir / "far"}',
        logging=action_logs(
            'wake',
            'chimera.commands.wake.wake',
            {'target': 'p@g@agent', 'goal': None, 'actor': None, 'dry': False, 'project': None},
        ),
    )


def test_wake_cli_fenced_manager_cannot_reach_by_session_name(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    # a scoped manager wakes by goal, inside its fence — a target that isn't one of its
    # goals never falls through to the cross-project session lookup
    _project_with_worktree(tmpdir)
    _stub(replace, [_parked(tmpdir / 'far')])
    replace.in_environ('CHIMERA_ROLE', 'manager')
    replace.in_environ('CHIMERA_ROLE_SCOPE', 'myproject')
    command.run('wake', 'other@x@agent').check(
        output="Error: 'other@x@agent' is not a valid goal name: '@' separates goal from actor",
        return_code=1,
        logging=action_logs(
            'wake',
            'chimera.commands.wake.wake',
            {
                'target': 'other@x@agent',
                'goal': None,
                'actor': None,
                'dry': False,
                'project': None,
            },
            error="UserError: 'other@x@agent' is not a valid goal name: "
            "'@' separates goal from actor",
        ),
    )
