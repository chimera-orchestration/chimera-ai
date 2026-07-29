import os
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from testfixtures import Replacer, TempDir, compare

from chimera import __main__ as chimera_main
from chimera.agents import AgentSession
from chimera.archive import Archive
from chimera.archive import ArchiveSession
from chimera.commands.agent import agents
from chimera.commands.ls import Board, GoalBoard, Mail, ProjectBoard, Row, board
from chimera.comms import Comms, compose
from chimera.context import Scope, resolve_project
from tests.cli import Command, action_logs

NOON = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
_NO_MAIL = Mail(0, 0, 0)


def _project(tmpdir: TempDir, ws: Path, name: str, *goals: str) -> Path:
    project = ws / name
    tmpdir.dump(
        project / 'config.yaml',
        {'kind': 'project', 'repo': str(project)},
    )
    for goal in goals:
        (project / 'worktrees' / f'{goal}@agent').mkdir(parents=True)
    return project


def _agent(
    cwd: Path, name: str, status: str = 'idle', summary: str | None = None, id: str = 'id'
) -> AgentSession:
    return AgentSession(id, name, status, cwd, summary)


def _record(
    ws: Path,
    native_id: str,
    *,
    address: str | None = None,
    project: str | None = None,
    goal: str | None = None,
    actor: str | None = None,
    started_at: datetime = NOON,
) -> None:
    """Seed one archived session row directly, bypassing the SessionStart hook."""
    with Archive.open(ws / 'state' / 'archive.db') as store:
        store.record_session(
            ArchiveSession(
                platform='claude',
                native_id=native_id,
                status='ended',
                started_at=started_at,
                address=address,
                workspace=ws.name,
                project=project,
                goal=goal,
                actor=actor,
            )
        )


def _send(ws: Path, to: str, *, state: str = 'new') -> None:
    """Seed one message to ``to``, left in ``state`` (``new``/``cur``/``done``)."""
    store = Comms(ws / 'state' / 'mail')
    sent = store.send(compose(sender='x', to=to, kind='message', subject='s', body=''))
    if state in ('cur', 'done'):
        store.drain(to)
    if state == 'done':
        store.dispose(to, sent.id)


@pytest.fixture()
def store(workspace: Path) -> Iterator[Archive]:
    with Archive.open(workspace / 'state' / 'archive.db') as a:
        yield a


@pytest.fixture()
def mailbox(workspace: Path) -> Comms:
    return Comms(workspace / 'state' / 'mail')


class TestBoard:
    def test_partitions_agents_into_goals_project_loose_and_workspace_loose(
        self, tmpdir: TempDir, workspace: Path, store: Archive, mailbox: Comms
    ) -> None:
        _project(tmpdir, workspace, 'alpha', 'g')
        in_goal = _agent(workspace / 'alpha' / 'worktrees' / 'g@agent', 'alpha@g@agent', 'busy')
        in_repo = _agent(
            workspace / 'alpha' / 'repo', 'loose-proj'
        )  # under the project, not a goal
        stray = _agent(workspace / 'scratch', 'stray-ws')  # under the workspace, not a project
        outside = _agent(tmpdir / 'elsewhere', 'outside')  # filtered out entirely
        result = board(
            Scope(workspace, None, None), [in_goal, in_repo, stray, outside], store, mailbox
        )
        compare(
            result,
            expected=Board(
                workspace='lycia',
                captain=Row('@@captain', None, None, _NO_MAIL),
                projects=[
                    ProjectBoard(
                        'alpha',
                        Row('alpha@@manager', None, None, _NO_MAIL),
                        [GoalBoard('g', [Row('alpha@g@agent', in_goal, None, _NO_MAIL)])],
                        [in_repo],
                    )
                ],
                loose=[stray],
                history=[],
                history_withheld=0,
            ),
        )

    def test_pinned_goal_shows_only_that_goal(
        self, tmpdir: TempDir, workspace: Path, store: Archive, mailbox: Comms
    ) -> None:
        _project(tmpdir, workspace, 'alpha', 'g', 'other')
        project = resolve_project(workspace / 'alpha')
        a = _agent(workspace / 'alpha' / 'worktrees' / 'g@agent', 'a')
        b = _agent(workspace / 'alpha' / 'worktrees' / 'other@agent', 'b')
        result = board(Scope(workspace, project, 'g'), [a, b], store, mailbox)
        compare(
            result,
            expected=Board(
                'lycia',
                Row('@@captain', None, None, _NO_MAIL),
                [
                    ProjectBoard(
                        'alpha',
                        Row('alpha@@manager', None, None, _NO_MAIL),
                        [GoalBoard('g', [Row('alpha@g@agent', a, None, _NO_MAIL)])],
                        [],
                    )
                ],
                [],
                [],
                0,
            ),
        )

    def test_captain_row_falls_back_to_the_archive_when_nothing_is_live(
        self, workspace: Path, store: Archive, mailbox: Comms
    ) -> None:
        _record(workspace, 's1', address='@@captain')
        result = board(Scope(workspace, None, None), [], store, mailbox)
        assert result.captain.live is None
        assert result.captain.last is not None
        assert result.captain.last.native_id == 's1'

    def test_manager_row_falls_back_to_the_archive(
        self, tmpdir: TempDir, workspace: Path, store: Archive, mailbox: Comms
    ) -> None:
        _project(tmpdir, workspace, 'alpha')
        _record(workspace, 's1', address='alpha@@manager', project='alpha')
        result = board(Scope(workspace, None, None), [], store, mailbox)
        [project] = result.projects
        assert project.manager.last is not None
        assert project.manager.last.native_id == 's1'

    def test_goal_actors_union_live_and_archived(
        self, tmpdir: TempDir, workspace: Path, store: Archive, mailbox: Comms
    ) -> None:
        _project(tmpdir, workspace, 'alpha', 'g')  # only an agent worktree exists
        _record(
            workspace,
            's1',
            address='alpha@g@reviewer',  # unrestricted for goal actors — any session in the worktree counts
            project='alpha',
            goal='g',
            actor='reviewer',
        )
        result = board(Scope(workspace, None, None), [], store, mailbox)
        [project] = result.projects
        [goal] = project.goals
        compare(
            sorted(row.address for row in goal.actors),
            expected=['alpha@g@agent', 'alpha@g@reviewer'],
        )
        reviewer = next(row for row in goal.actors if row.address == 'alpha@g@reviewer')
        assert reviewer.last is not None and reviewer.last.native_id == 's1'
        agent = next(row for row in goal.actors if row.address == 'alpha@g@agent')
        assert agent.live is None and agent.last is None  # never run

    def test_mail_counts_land_on_the_matching_row(
        self, tmpdir: TempDir, workspace: Path, store: Archive, mailbox: Comms
    ) -> None:
        _project(tmpdir, workspace, 'alpha', 'g')
        in_goal = _agent(workspace / 'alpha' / 'worktrees' / 'g@agent', 'alpha@g@agent')
        # drain() claims every undrained message for the address, so the ones due to end up
        # 'cur'/'done' must be sent (and drained/disposed) before the ones meant to stay 'new'
        _send(workspace, 'alpha@g@agent', state='done')
        _send(workspace, 'alpha@g@agent', state='cur')
        _send(workspace, 'alpha@g@agent', state='new')
        _send(workspace, 'alpha@g@agent', state='new')
        result = board(Scope(workspace, None, None), [in_goal], store, mailbox)
        [project] = result.projects
        [goal] = project.goals
        [row] = goal.actors
        compare(row.mail, expected=Mail(new=2, cur=1, done=1))

    def test_history_surfaces_unclaimed_archived_sessions(
        self, workspace: Path, store: Archive, mailbox: Comms
    ) -> None:
        _record(workspace, 'ghost')  # no project/goal — unaddressed system run
        result = board(Scope(workspace, None, None), [], store, mailbox)
        compare([row.last.native_id for row in result.history if row.last], expected=['ghost'])

    def test_history_omits_an_old_resume_of_a_currently_live_actor(
        self, tmpdir: TempDir, workspace: Path, store: Archive, mailbox: Comms
    ) -> None:
        # a goal actor resumed more than once accrues one archive row per resume, all
        # sharing the address's name — only the exact live occupant used to get excluded,
        # leaving every earlier resume to leak into history under the same display name
        _project(tmpdir, workspace, 'alpha', 'g')
        worktree = workspace / 'alpha' / 'worktrees' / 'g@agent'
        _record(
            workspace,
            'earlier-resume',
            address='alpha@g@agent',
            project='alpha',
            goal='g',
            actor='agent',
        )
        live = _agent(worktree, 'alpha@g@agent', id='current-native-id')
        result = board(Scope(workspace, None, None), [live], store, mailbox)
        assert result.history == []

    def test_history_is_bounded_and_signals_withheld(
        self, workspace: Path, store: Archive, mailbox: Comms
    ) -> None:
        from chimera.commands.ls import HISTORY_LIMIT

        for i in range(HISTORY_LIMIT + 2):
            _record(workspace, f's{i}', started_at=NOON)
        result = board(Scope(workspace, None, None), [], store, mailbox)
        assert len(result.history) == HISTORY_LIMIT
        assert result.history_withheld == 1


def test_ls_cli_renders_the_tree(
    tmpdir: TempDir, workspace_with_env: Path, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, workspace_with_env, 'alpha', 'g')
    worktree = workspace_with_env / 'alpha' / 'worktrees' / 'g@agent'
    replace.in_module(
        agents,
        lambda: [
            AgentSession('012a9550', 'alpha@g@agent', 'busy', worktree, 'fix the bug'),
            # a stale-marked corpse never lands on the dashboard — agent ls -v is its surface
            AgentSession('deadbeef', 'ghost', 'idle', worktree, None, stale='claimed pid 9 dead'),
        ],
        module=chimera_main,
    )
    command.run('ls').check(
        output='\n'.join(
            [
                'lycia',
                '  @@captain  (never run)',
                '  alpha',
                '    alpha@@manager  (never run)',
                '    g',
                '      alpha@g@agent  012a9550  busy  fix the bug',
            ]
        ),
        logging=action_logs('ls', 'chimera.commands.ls.board', {'project': None, 'goal': None}),
    )


def test_ls_cli_renders_loose_agents(
    tmpdir: TempDir, workspace_with_env: Path, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, workspace_with_env, 'alpha')  # no goals; a session in repo/ is project-loose
    stray = workspace_with_env / 'scratch'  # under the workspace but no project → board-loose
    replace.in_module(
        agents,
        lambda: [
            AgentSession(
                '012a9550', 'repo-sess', 'busy', workspace_with_env / 'alpha' / 'repo', 'building'
            ),
            AgentSession('39d68dfa', 'stray', 'idle', stray, None),
        ],
        module=chimera_main,
    )
    command.run('ls').check(
        output='\n'.join(
            [
                'lycia',
                '  @@captain  (never run)',
                '  alpha',
                '    alpha@@manager  (never run)',
                '    · 012a9550  repo-sess  busy  building',
                f'  · 39d68dfa  stray  idle  {stray}',
            ]
        ),
        logging=action_logs('ls', 'chimera.commands.ls.board', {'project': None, 'goal': None}),
    )


def test_ls_cli_stays_global_from_inside_a_project(
    tmpdir: TempDir, workspace_with_env: Path, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, workspace_with_env, 'alpha', 'g')
    _project(tmpdir, workspace_with_env, 'beta')
    os.chdir(workspace_with_env / 'alpha')  # standing in a project must not narrow the dashboard
    replace.in_module(agents, list, module=chimera_main)
    command.run('ls').check(
        output='\n'.join(
            [
                'lycia',
                '  @@captain  (never run)',
                '  alpha',
                '    alpha@@manager  (never run)',
                '    g',
                '      alpha@g@agent  (never run)',
                '  beta',
                '    beta@@manager  (never run)',
                '    (no goals)',
            ]
        ),
        logging=action_logs('ls', 'chimera.commands.ls.board', {'project': None, 'goal': None}),
    )


def test_ls_cli_marks_empty_goals_and_projects(
    tmpdir: TempDir, workspace_with_env: Path, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, workspace_with_env, 'alpha', 'g')
    _project(tmpdir, workspace_with_env, 'beta')
    replace.in_module(agents, list, module=chimera_main)
    command.run('ls').check(
        output='\n'.join(
            [
                'lycia',
                '  @@captain  (never run)',
                '  alpha',
                '    alpha@@manager  (never run)',
                '    g',
                '      alpha@g@agent  (never run)',
                '  beta',
                '    beta@@manager  (never run)',
                '    (no goals)',
            ]
        ),
        logging=action_logs('ls', 'chimera.commands.ls.board', {'project': None, 'goal': None}),
    )


def test_ls_cli_shows_mail_and_archive_fallback(
    tmpdir: TempDir, workspace_with_env: Path, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, workspace_with_env, 'alpha')
    replace.in_module(agents, list, module=chimera_main)
    _record(workspace_with_env, 's1', address='alpha@@manager', project='alpha')
    _send(workspace_with_env, 'alpha@@manager', state='new')
    command.run('ls').check(
        output='\n'.join(
            [
                'lycia',
                '  @@captain  (never run)',
                '  alpha',
                '    alpha@@manager  s1  ended  mail 1n',
                '    (no goals)',
            ]
        ),
        logging=action_logs('ls', 'chimera.commands.ls.board', {'project': None, 'goal': None}),
    )


def test_ls_cli_shows_history(
    workspace_with_env: Path, replace: Replacer, command: Command
) -> None:
    # an unaddressed archived session falls through to history, identified by its id
    replace.in_module(agents, list, module=chimera_main)
    _record(workspace_with_env, 'ghost1')
    command.run('ls').check(
        output='\n'.join(
            [
                'lycia',
                '  @@captain  (never run)',
                '  · ghost1  ghost1  ended',
            ]
        ),
        logging=action_logs('ls', 'chimera.commands.ls.board', {'project': None, 'goal': None}),
    )


def test_ls_cli_signposts_withheld_history(
    workspace_with_env: Path, replace: Replacer, command: Command
) -> None:
    from chimera.commands.ls import HISTORY_LIMIT

    replace.in_module(agents, list, module=chimera_main)
    for i in range(HISTORY_LIMIT + 1):
        _record(workspace_with_env, f'ghost{i}', started_at=NOON)
    result = command.run('ls')
    hint = '(+more archived sessions not shown — ch dashboard for the full view)'
    assert hint in result.output.captured
