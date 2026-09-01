import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from testfixtures import LogCapture, Replacer, TempDir, compare

from chimera.archive import LAUNCH_WINDOW, Archive, ArchiveSession, Event, PendingLaunch
from chimera.agents import AgentSession
from chimera.agents.claude import Claude
from chimera.commands.hook.capture import session_end, session_start
from tests.cli import Command, action_logs

START = 'chimera.commands.hook.capture.session_start'
END = 'chimera.commands.hook.capture.session_end'


@pytest.fixture(autouse=True)
def _nothing_running(replace: Replacer) -> None:
    """No session is live anywhere unless a test says so.

    Every start now asks who else is working in that cwd (the crowding warning), which
    means the registry — so a test that doesn't care must still not shell out to it.
    """
    replace.on_class(Claude.live, lambda self, cwd=None: [])


def _archived(ws: Path) -> list[ArchiveSession]:
    with Archive.open(ws / 'state' / 'archive.db') as a:
        return a.sessions()


def _events(ws: Path, session_id: str) -> list[Event]:
    with Archive.open(ws / 'state' / 'archive.db') as a:
        return a.events(platform='claude', native_id=session_id)


def _start(
    cwd: Path,
    session_id: str,
    source: str = 'startup',
    *,
    transcript: str | None = None,
    env: dict[str, str] | None = None,
    **payload: object,
) -> None:
    """Fire a SessionStart the way claude would: a payload plus the process environment."""
    session_start(
        Claude(),
        {
            'cwd': str(cwd),
            'session_id': session_id,
            'transcript_path': transcript if transcript is not None else f'/t/{session_id}.jsonl',
            'source': source,
            **payload,
        },
        env if env is not None else {},
    )


def test_session_start_records_the_session(tmpdir: TempDir, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace', 'captain': 'pegasus'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    _start(ws, 'uuid-1', 'startup')
    [session] = _archived(ws)
    assert (session.platform, session.native_id) == ('claude', 'uuid-1')
    assert (session.status, session.workspace) == ('startup', 'ws')
    # standing in the workspace root is not evidence of being its captain
    assert session.address is None


def test_session_start_records_the_model(tmpdir: TempDir, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace', 'captain': 'pegasus'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    _start(ws, 'uuid-1', 'startup', model='claude-sonnet-5')
    [session] = _archived(ws)
    assert session.model == 'claude-sonnet-5'


def test_session_start_in_a_goal_worktree_sets_the_axes(tmpdir: TempDir, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    tmpdir.dump('ws/proj/config.yaml', {'kind': 'project', 'repo': '/r'})
    worktree = tmpdir.path / 'ws' / 'proj' / 'worktrees' / 'g@agent'
    worktree.mkdir(parents=True)
    replace.in_environ('CHIMERA_WORKSPACE', str(tmpdir / 'ws'))
    _start(worktree, 'uuid-2', 'startup')
    [session] = _archived(tmpdir.path / 'ws')
    # the axes record where it sat; a hand-launched claude here still claims nothing
    assert (session.project, session.goal, session.actor) == ('proj', 'g', 'agent')
    assert session.address is None


def test_session_start_in_a_reviewer_worktree_records_the_true_actor(
    tmpdir: TempDir, replace: Replacer
) -> None:
    # the actor comes from the <goal>@<actor> dir, never assumed: a reviewer session
    # archived as the agent's would hand `agent resume` the wrong conversation
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    tmpdir.dump('ws/proj/config.yaml', {'kind': 'project', 'repo': '/r'})
    worktree = tmpdir.path / 'ws' / 'proj' / 'worktrees' / 'g@reviewer'
    worktree.mkdir(parents=True)
    # the goal is known through its agent worktree (see worktrees.goals); the reviewer's
    # sits alongside, as `ch worktree add --actor` leaves things
    (tmpdir.path / 'ws' / 'proj' / 'worktrees' / 'g@agent').mkdir()
    replace.in_environ('CHIMERA_WORKSPACE', str(tmpdir / 'ws'))
    _start(worktree, 'uuid-r', 'startup')
    [session] = _archived(tmpdir.path / 'ws')
    assert (session.project, session.goal, session.actor) == ('proj', 'g', 'reviewer')


def test_session_start_records_the_harness_version(tmpdir: TempDir, replace: Replacer) -> None:
    # which build produced the row: a version sessions.md has never validated is the alarm
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    _start(ws, 'uuid-3', 'startup', env={'AI_AGENT': 'claude-code_2-1-220_agent'})
    assert _archived(ws)[0].harness_version == 'claude-code_2-1-220_agent'


def test_session_start_without_a_harness_version_records_none(
    tmpdir: TempDir, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    _start(ws, 'uuid-4', 'startup')
    assert _archived(ws)[0].harness_version is None


def test_session_start_outside_a_workspace_is_a_noop(tmpdir: TempDir) -> None:
    _start(tmpdir.path / 'nowhere', 'uuid-x', 'startup')
    assert not (tmpdir.path / 'nowhere' / 'state').exists()


def test_session_end_marks_the_session_ended(tmpdir: TempDir, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_ROLE', '')
    _start(ws, 'uuid-1', 'startup')
    session_end(ws, 'uuid-1', 'logout')
    [session] = _archived(ws)
    assert session.status == 'logout'
    assert session.ended_at is not None


def test_a_tui_draft_session_never_acquires_a_mail_address(
    tmpdir: TempDir, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace', 'captain': 'pegasus'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    _start(ws, 'uuid-tui', 'startup', agent_type='claude')
    [session] = _archived(ws)
    assert session.address is None  # recorded, but no address for mail to route to
    assert session.workspace == 'ws'  # the location facts survive


def test_a_one_shot_print_run_never_acquires_a_mail_address(
    tmpdir: TempDir, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    tmpdir.dump('ws/proj/config.yaml', {'kind': 'project', 'repo': '/r'})
    worktree = tmpdir.path / 'ws' / 'proj' / 'worktrees' / 'g@agent'
    worktree.mkdir(parents=True)
    replace.in_environ('CHIMERA_WORKSPACE', str(tmpdir / 'ws'))
    _start(worktree, 'uuid-p', 'startup', env={'CLAUDE_CODE_ENTRYPOINT': 'sdk-cli'})
    [session] = _archived(tmpdir.path / 'ws')
    assert session.address is None
    assert not session.addressable  # …and never eligible for one, whatever it does next
    assert (session.project, session.goal) == ('proj', 'g')  # where it ran is still on record


def test_a_resumed_session_keeps_its_row_and_gains_the_history(
    tmpdir: TempDir, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_ROLE', '')
    _start(ws, 'uuid-1', 'startup')
    started = _archived(ws)[0].started_at
    session_end(ws, 'uuid-1', 'other')
    _start(ws, 'uuid-1', 'resume')
    [session] = _archived(ws)  # one row per identity, however many lives
    assert session.started_at == started
    assert session.ended_at is None
    assert session.status == 'resume'
    timeline = [(e.kind, e.detail) for e in _events(ws, 'uuid-1')]
    assert timeline == [('startup', None), ('end', 'other'), ('resume', None)]


def test_session_end_for_a_session_the_hooks_never_saw_records_no_event(
    tmpdir: TempDir, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    session_end(ws, 'pre-hook-uuid', 'other')  # no crash — nothing to stitch an event to
    assert _archived(ws) == []
    assert _events(ws, 'pre-hook-uuid') == []


def test_session_end_outside_a_workspace_is_a_noop(tmpdir: TempDir) -> None:
    session_end(tmpdir.path / 'nowhere', 'uuid-x', 'logout')  # no crash, nothing written
    assert not (tmpdir.path / 'nowhere' / 'state').exists()


def test_hook_session_start_cli(tmpdir: TempDir, command: Command, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_ROLE', '')
    payload = (
        f'{{"cwd": "{ws}", "session_id": "uuid-1", '
        f'"transcript_path": "/t/uuid-1.jsonl", "source": "startup"}}'
    )
    replace(target=sys.stdin, container=sys, name='stdin', replacement=io.StringIO(payload))
    command.run('hook', 'session-start').check(
        output='', logging=action_logs('hook session-start', START, {})
    )
    assert _archived(ws)[0].native_id == 'uuid-1'


def test_hook_session_start_cli_reads_the_model_from_the_payload(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_ROLE', '')
    payload = (
        f'{{"cwd": "{ws}", "session_id": "uuid-1", "transcript_path": "/t/uuid-1.jsonl", '
        f'"source": "startup", "model": "claude-sonnet-5"}}'
    )
    replace(target=sys.stdin, container=sys, name='stdin', replacement=io.StringIO(payload))
    command.run('hook', 'session-start').check(
        output='', logging=action_logs('hook session-start', START, {})
    )
    assert _archived(ws)[0].model == 'claude-sonnet-5'


def test_hook_session_start_cli_fences_on_payload_and_environment(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_ROLE', '')
    replace.in_environ('CLAUDE_CODE_ENTRYPOINT', 'sdk-cli')
    payload = (
        f'{{"cwd": "{ws}", "session_id": "uuid-p", '
        f'"transcript_path": "/t/uuid-p.jsonl", "source": "startup", "agent_type": "claude"}}'
    )
    replace(target=sys.stdin, container=sys, name='stdin', replacement=io.StringIO(payload))
    start, end = action_logs('hook session-start', START, {})
    command.run('hook', 'session-start').check(
        output='',
        logging=[
            start,
            {
                'level': 'INFO',
                'session_id': 'uuid-p',
                'lifecycle': 'startup',
                'message': 'hook session-start: not a conversation, recording without an address',
            },
            end,
        ],
    )
    assert _archived(ws)[0].address is None  # both signals read: payload agent_type, env entrypoint


def test_hook_session_end_cli(tmpdir: TempDir, command: Command, replace: Replacer) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_ROLE', '')
    _start(ws, 'uuid-1', 'startup')
    payload = f'{{"cwd": "{ws}", "session_id": "uuid-1", "reason": "logout"}}'
    replace(target=sys.stdin, container=sys, name='stdin', replacement=io.StringIO(payload))
    command.run('hook', 'session-end').check(
        output='', logging=action_logs('hook session-end', END, {})
    )
    assert _archived(ws)[0].status == 'logout'


def test_hook_session_start_cli_surfaces_unmodeled_payload_keys(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    # hook_event_name is known-and-ignored; parent_session_id stands in for whatever
    # the harness starts sending next — surfaced with its value, never silently dropped
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_ROLE', '')
    payload = (
        f'{{"cwd": "{ws}", "session_id": "uuid-f", "transcript_path": "/t/uuid-f.jsonl", '
        f'"source": "fork", "hook_event_name": "SessionStart", '
        f'"parent_session_id": "uuid-parent"}}'
    )
    replace(target=sys.stdin, container=sys, name='stdin', replacement=io.StringIO(payload))
    start, end = action_logs('hook session-start', START, {})
    command.run('hook', 'session-start').check(
        output='',
        logging=[
            start,
            {
                'level': 'INFO',
                'session_id': 'uuid-f',
                'payload': {'parent_session_id': 'uuid-parent'},
                'message': 'hook session-start: unmodeled payload keys',
            },
            end,
        ],
    )


def test_hook_session_end_cli_surfaces_unmodeled_payload_keys(
    tmpdir: TempDir, command: Command, replace: Replacer
) -> None:
    tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
    ws = tmpdir.path / 'ws'
    replace.in_environ('CHIMERA_WORKSPACE', str(ws))
    replace.in_environ('CHIMERA_ROLE', '')
    _start(ws, 'uuid-1', 'startup')
    payload = (
        f'{{"cwd": "{ws}", "session_id": "uuid-1", "reason": "other", '
        f'"hook_event_name": "SessionEnd", "bridge_session_id": "cse_abc"}}'
    )
    replace(target=sys.stdin, container=sys, name='stdin', replacement=io.StringIO(payload))
    start, end = action_logs('hook session-end', END, {})
    command.run('hook', 'session-end').check(
        output='',
        logging=[
            start,
            {
                'level': 'INFO',
                'session_id': 'uuid-1',
                'payload': {'bridge_session_id': 'cse_abc'},
                'message': 'hook session-end: unmodeled payload keys',
            },
            end,
        ],
    )


class TestAddressIsClaimedOnEvidence:
    # the rule this whole design turns on: a location never entitles a session to an
    # address. Only a launch chimera itself recorded, or inheritance across a bridge.

    def _worktree(self, tmpdir: TempDir, replace: Replacer) -> Path:
        tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
        tmpdir.dump('ws/proj/config.yaml', {'kind': 'project', 'repo': '/r'})
        worktree = tmpdir.path / 'ws' / 'proj' / 'worktrees' / 'g@agent'
        worktree.mkdir(parents=True)
        replace.in_environ('CHIMERA_WORKSPACE', str(tmpdir / 'ws'))
        return worktree

    def _record_launch(self, ws: Path, cwd: Path, address: str, model: str | None = None) -> None:
        with Archive.open(ws / 'state' / 'archive.db') as store:
            store.record_launch(
                PendingLaunch(
                    at=datetime.now(timezone.utc),
                    platform='claude',
                    cwd=cwd,
                    address=address,
                    model=model,
                )
            )

    def test_a_compact_claims_nothing(self, tmpdir: TempDir, replace: Replacer) -> None:
        # /compact and /clear fire SessionStart for a session that already exists. Treated
        # as cold starts they consumed the launch record belonging to a session genuinely
        # starting in that directory, leaving the real one unaddressed
        worktree = self._worktree(tmpdir, replace)
        ws = tmpdir.path / 'ws'
        self._record_launch(ws, worktree, 'proj@g@agent')
        _start(worktree, 'uuid-compacting', 'compact')
        _start(worktree, 'uuid-the-real-launch', 'startup')
        addressed = {s.native_id: s.address for s in _archived(ws)}
        compare(
            addressed,
            expected={'uuid-compacting': None, 'uuid-the-real-launch': 'proj@g@agent'},
        )

    def test_a_recorded_launch_is_claimed(self, tmpdir: TempDir, replace: Replacer) -> None:
        worktree = self._worktree(tmpdir, replace)
        ws = tmpdir.path / 'ws'
        self._record_launch(ws, worktree, 'proj@g@agent', model='opus')
        _start(worktree, 'uuid-a', 'startup')
        [session] = _archived(ws)
        compare((session.address, session.model), expected=('proj@g@agent', 'opus'))

    def test_a_launch_is_claimed_only_once(self, tmpdir: TempDir, replace: Replacer) -> None:
        # two sessions starting in one worktree must not both take the address
        worktree = self._worktree(tmpdir, replace)
        ws = tmpdir.path / 'ws'
        self._record_launch(ws, worktree, 'proj@g@agent')
        _start(worktree, 'uuid-a', 'startup')
        _start(worktree, 'uuid-b', 'startup')
        addressed = {s.native_id: s.address for s in _archived(ws)}
        compare(addressed, expected={'uuid-a': 'proj@g@agent', 'uuid-b': None})

    def test_a_stale_launch_is_never_claimed(self, tmpdir: TempDir, replace: Replacer) -> None:
        # a launch that never produced a session must not lie in wait for an unrelated one
        worktree = self._worktree(tmpdir, replace)
        ws = tmpdir.path / 'ws'
        with Archive.open(ws / 'state' / 'archive.db') as store:
            store.record_launch(
                PendingLaunch(
                    at=datetime.now(timezone.utc) - LAUNCH_WINDOW - timedelta(seconds=1),
                    platform='claude',
                    cwd=worktree,
                    address='proj@g@agent',
                )
            )
        _start(worktree, 'uuid-a', 'startup')
        assert _archived(ws)[0].address is None

    def test_a_launch_in_another_directory_is_not_claimed(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        worktree = self._worktree(tmpdir, replace)
        ws = tmpdir.path / 'ws'
        self._record_launch(ws, ws, '@@captain')  # the captain's launch, elsewhere
        _start(worktree, 'uuid-a', 'startup')
        assert _archived(ws)[0].address is None

    def test_a_branched_session_inherits_its_presumed_parent(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        # backgrounding mints a brand-new id, so without inheritance the chat's mail
        # would be silently orphaned the moment it was bridged
        worktree = self._worktree(tmpdir, replace)
        ws = tmpdir.path / 'ws'
        self._record_launch(ws, worktree, 'proj@g@agent')
        _start(worktree, 'uuid-parent', 'startup')
        _start(worktree, 'uuid-fork', 'fork')
        addressed = {s.native_id: s.address for s in _archived(ws)}
        compare(addressed, expected={'uuid-parent': 'proj@g@agent', 'uuid-fork': 'proj@g@agent'})

    def test_a_branch_of_an_unaddressed_session_inherits_nothing(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        worktree = self._worktree(tmpdir, replace)
        ws = tmpdir.path / 'ws'
        _start(worktree, 'uuid-parent', 'startup')  # raw session, no claim
        _start(worktree, 'uuid-fork', 'fork')
        assert all(s.address is None for s in _archived(ws))

    def test_a_resume_keeps_the_address_it_already_had(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        worktree = self._worktree(tmpdir, replace)
        ws = tmpdir.path / 'ws'
        self._record_launch(ws, worktree, 'proj@g@agent')
        _start(worktree, 'uuid-a', 'startup')
        _start(worktree, 'uuid-a', 'resume')
        [session] = _archived(ws)
        compare((session.address, session.status), expected=('proj@g@agent', 'resume'))

    def test_a_draft_never_claims_a_launch(self, tmpdir: TempDir, replace: Replacer) -> None:
        # the browser pre-spawns drafts around a bridge; one must not eat the address
        worktree = self._worktree(tmpdir, replace)
        ws = tmpdir.path / 'ws'
        self._record_launch(ws, worktree, 'proj@g@agent')
        _start(worktree, 'uuid-draft', 'startup', agent_type='claude')
        _start(worktree, 'uuid-real', 'startup')
        addressed = {s.native_id: s.address for s in _archived(ws)}
        compare(addressed, expected={'uuid-draft': None, 'uuid-real': 'proj@g@agent'})


class TestSessionEnd:
    def test_an_end_that_finds_no_row_says_so(
        self, tmpdir: TempDir, replace: Replacer, full_logs: LogCapture
    ) -> None:
        # the row is written under whatever `identity` anchored on — the transcript stem.
        # An end keyed by the payload id instead was an UPDATE matching nothing: no
        # warning, no event, and a row left open forever, outranking every real resume
        # candidate. The divergence that motivates this is observed, not hypothetical
        tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
        ws = tmpdir.path / 'ws'
        replace.in_environ('CHIMERA_WORKSPACE', str(ws))
        session_end(ws, 'a-session-nobody-recorded', 'prompt_input_exit')
        compare(_archived(ws), expected=[])
        full_logs.check_present(
            {
                'level': 'WARNING',
                'session': 'a-session-nobody-recorded',
                'cwd': str(ws),
                'message': 'hook session-end: no recorded session, nothing to close',
            }
        )

    def test_an_end_closes_the_session_it_names(self, tmpdir: TempDir, replace: Replacer) -> None:
        tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
        ws = tmpdir.path / 'ws'
        replace.in_environ('CHIMERA_WORKSPACE', str(ws))
        _start(ws, 'uuid-a', 'startup')
        session_end(ws, 'uuid-a', 'prompt_input_exit')
        [session] = _archived(ws)
        assert session.ended_at is not None
        compare(session.status, expected='prompt_input_exit')


class TestTranscript:
    def test_a_payload_naming_no_transcript_records_none(
        self, tmpdir: TempDir, replace: Replacer
    ) -> None:
        # Path('') is Path('.') — truthy, and it *exists*. Stored, the row reads as
        # resumable and gets handed to `claude --resume`, which is the "No conversation
        # found" traceback this whole design started from; it also fails the harness
        # contract check forever, since Path('.').stem can never be a session id
        tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
        ws = tmpdir.path / 'ws'
        replace.in_environ('CHIMERA_WORKSPACE', str(ws))
        _start(ws, 'uuid-a', 'startup', transcript='')
        [session] = _archived(ws)
        assert session.transcript is None
        assert not session.transcript_missing  # nothing recorded, so nothing lost

    def test_a_named_transcript_is_kept(self, tmpdir: TempDir, replace: Replacer) -> None:
        tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
        ws = tmpdir.path / 'ws'
        replace.in_environ('CHIMERA_WORKSPACE', str(ws))
        _start(ws, 'uuid-a', 'startup', transcript='/t/uuid-a.jsonl')
        [session] = _archived(ws)
        compare(session.transcript, expected=Path('/t/uuid-a.jsonl'))


class TestCrowdingWarning:
    # the launchers refuse a second writer, but they only see launches *they* make: a raw
    # claude or a browser attach arrives with the session already running, and a
    # SessionStart hook cannot turn one away — so the most it can do is say so

    @pytest.fixture(autouse=True)
    def _nothing_running(self) -> None:
        """Overrides the module's blanket stub: these tests replace it themselves."""

    def _live(self, replace: Replacer, cwd: Path) -> list[str]:
        """The ids the registry reports live in ``cwd`` — append to change what's running.

        One replacement, mutated in place: replacing the same attribute twice hands the
        second call the stand-in rather than the method (testfixtures#259).
        """
        running: list[str] = []
        replace.on_class(
            Claude.live,
            lambda self, c=None: [AgentSession(i, i, 'idle', cwd, None) for i in running],
        )
        return running

    def test_a_cold_start_beside_a_working_agent_warns(
        self, tmpdir: TempDir, replace: Replacer, full_logs: LogCapture
    ) -> None:
        tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
        ws = tmpdir.path / 'ws'
        replace.in_environ('CHIMERA_WORKSPACE', str(ws))
        running = self._live(replace, ws)  # nothing live yet: the first start warns about nothing
        _start(ws, 'first', 'startup')
        running.append('first')
        _start(ws, 'second', 'startup')
        full_logs.check_present(
            {
                'level': 'WARNING',
                'message': 'hook session-start: another agent is already working here',
                'session': 'second',
                'cwd': str(ws),
                'occupants': ['first'],
            }
        )

    def test_a_branched_session_is_not_crowding_its_own_parent(
        self, tmpdir: TempDir, replace: Replacer, full_logs: LogCapture
    ) -> None:
        tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
        ws = tmpdir.path / 'ws'
        replace.in_environ('CHIMERA_WORKSPACE', str(ws))
        running = self._live(replace, ws)
        _start(ws, 'parent', 'startup')
        running.append('parent')
        _start(ws, 'fork', 'fork')
        assert not [entry for entry in full_logs.actual() if entry.get('level') == 'WARNING']

    def test_an_empty_worktree_warns_about_nothing(
        self, tmpdir: TempDir, replace: Replacer, full_logs: LogCapture
    ) -> None:
        tmpdir.dump('ws/config.yaml', {'kind': 'workspace'})
        ws = tmpdir.path / 'ws'
        replace.in_environ('CHIMERA_WORKSPACE', str(ws))
        self._live(replace, ws)
        _start(ws, 'only', 'startup')
        assert not [entry for entry in full_logs.actual() if entry.get('level') == 'WARNING']
