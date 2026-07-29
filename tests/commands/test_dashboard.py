import re
from datetime import datetime, timezone
from pathlib import Path

from testfixtures import Replacer, TempDir

from chimera import __main__ as chimera_main
from chimera.agents import AgentSession
from chimera.archive import ArchiveSession
from chimera.commands.agent import agents
from chimera.commands.dashboard import render
from chimera.commands.ls import Board, GoalBoard, Mail, ProjectBoard, Row
from tests.cli import Command, action_logs

NOON = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)

_ANSI = re.compile(r'\x1b\[[0-9;]*m')


def _strip(text: str) -> str:
    return _ANSI.sub('', text)


def _project(tmpdir: TempDir, ws: Path, name: str, *goals: str) -> Path:
    project = ws / name
    tmpdir.dump(project / 'config.yaml', {'kind': 'project', 'repo': str(project)})
    for goal in goals:
        (project / 'worktrees' / f'{goal}@agent').mkdir(parents=True)
    return project


class TestRender:
    def test_a_never_run_row_shows_the_placeholder(self) -> None:
        row = Row('alpha@g@agent', None, None, Mail(0, 0, 0))
        b = Board('lycia', row, [], [], [], 0)
        assert 'never run' in _strip(render(b))

    def test_the_header_and_status_are_colorized(self) -> None:
        row = Row('alpha@g@agent', None, None, Mail(0, 0, 0))
        b = Board('lycia', row, [], [], [], 0)
        text = render(b)
        assert '\x1b[' in text  # some ANSI styling present
        assert _strip(text) != text  # …and it's not visible in the stripped form

    def test_mail_columns_align_for_single_and_multi_digit_counts(self) -> None:
        live = AgentSession('id', 'alpha@g@agent', 'busy', Path('/x'), 'working')
        wide = Row('alpha@g@agent', live, None, Mail(10, 0, 0))
        narrow_row = Row('alpha@h@agent', live, None, Mail(1, 0, 0))
        b = Board(
            'lycia',
            Row('captain', None, None, Mail(0, 0, 0)),
            [
                ProjectBoard(
                    'alpha',
                    Row('alpha@manager', None, None, Mail(0, 0, 0)),
                    [GoalBoard('g', [wide]), GoalBoard('h', [narrow_row])],
                    [],
                )
            ],
            [],
            [],
            0,
        )
        header, *rest = _strip(render(b)).splitlines()
        new_col = header.index('NEW')  # the header's own field marks where the column starts
        data_lines = [line for line in rest if 'alpha@g@agent' in line or 'alpha@h@agent' in line]
        assert len(data_lines) == 2
        # right-justified within the fixed-width column, so a 1- and 2-digit count both align
        assert data_lines[0][new_col : new_col + 3] == ' 10'
        assert data_lines[1][new_col : new_col + 3] == '  1'

    def test_an_archived_only_row_shows_its_last_known_status(self) -> None:
        last = ArchiveSession(
            platform='claude',
            native_id='abc123',
            status='ended',
            started_at=NOON,
        )
        row = Row('alpha@g@agent', None, last, Mail(0, 0, 0))
        b = Board('lycia', row, [], [], [], 0)
        output = _strip(render(b))
        assert 'ended' in output
        assert 'alpha@g@agent' in output

    def test_history_rows_render_under_a_history_header(self) -> None:
        last = ArchiveSession(
            platform='claude', native_id='ghost1', status='ended', started_at=NOON, address='ghost'
        )
        history_row = Row('ghost', None, last, Mail(0, 0, 0))
        b = Board('lycia', Row('captain', None, None, Mail(0, 0, 0)), [], [], [history_row], 0)
        output = _strip(render(b))
        assert 'history' in output
        assert 'ghost' in output


def test_dashboard_cli_renders_the_tree(
    tmpdir: TempDir, workspace_with_env: Path, replace: Replacer, command: Command
) -> None:
    _project(tmpdir, workspace_with_env, 'alpha', 'g')
    worktree = workspace_with_env / 'alpha' / 'worktrees' / 'g@agent'
    replace.in_module(
        agents,
        lambda: [AgentSession('012a9550', 'alpha@g@agent', 'busy', worktree, 'fix the bug')],
        module=chimera_main,
    )
    command.run('dashboard').check(
        output=(
            'NAME               STATUS     DETAIL       NEW  CUR  DONE\n'
            'lycia\n'
            '@@captain          never run                 ·    ·     ·\n'
            'alpha            \n'
            '  alpha@@manager   never run                 ·    ·     ·\n'
            '  g              \n'
            '    alpha@g@agent  busy       fix the bug    ·    ·     ·'
        ),
        logging=action_logs(
            'dashboard', 'chimera.commands.ls.board', {'project': None, 'goal': None}
        ),
    )
