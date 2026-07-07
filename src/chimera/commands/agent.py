import json
import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from chimera.context import Scope
from chimera.worktrees import SEP


@dataclass(frozen=True)
class Agent:
    """A live agent: its claude id, name, status, working directory and most recent prompt."""

    id: str
    name: str
    status: str
    cwd: Path
    summary: str | None

    @property
    def detail(self) -> str:
        """One-line description: the session title or last prompt, else the cwd."""
        if self.summary:
            return self.summary
        home = str(Path.home())
        cwd = str(self.cwd)
        return '~' + cwd[len(home) :] if cwd.startswith(home) else cwd


def agents(projects: Path | None = None) -> list[Agent]:
    """Every live agent across all projects, each enriched with a one-line summary.

    The summary is the agent's session title or last prompt (see ``session_summary``),
    read from its transcript under ``projects`` (default ``~/.claude/projects``).
    """
    return [_describe(session, projects) for session in all_sessions()]


def _describe(session: dict[str, object], projects: Path | None) -> Agent:
    # `id` is claude's short session handle; fall back to the leading block of the
    # full sessionId (its own first 8 chars) when a session omits it.
    id = str(session.get('id') or session.get('sessionId') or '?')[:8]
    name = str(session.get('name') or id)
    cwd = str(session.get('cwd') or '')
    return Agent(
        id=id,
        name=name,
        status=str(session.get('status') or session.get('state') or '?'),
        cwd=Path(cwd),
        summary=session_summary(cwd, name, projects) if cwd else None,
    )


def scope_line(scope: Scope) -> str:
    """The banner shown above ``agent ls`` — what the list below is bounded to.

    ``scope: <project>@<goal>`` when both are pinned (mirroring the ``<project>@<goal>@<actor>``
    agent names in the rows), ``scope: <project>`` for a whole project, ``scope: all agents``
    for the unbounded global list. The stable ``scope:`` key stays greppable for agents while
    reading naturally for humans.
    """
    if scope.project is not None and scope.goal is not None:
        target = f'{scope.project.name}{SEP}{scope.goal}'
    elif scope.project is not None:
        target = scope.project.name
    else:
        target = 'all agents'
    return f'scope: {target}'


def scoped(listing: list[Agent], scope: Scope, *, otherwise: Path | None) -> list[Agent]:
    """The agents in scope: under the goal's worktrees, the project, else ``otherwise``.

    With no project pinned the fallback ``otherwise`` decides reach: ``None`` keeps every
    agent (``agent ls`` is the global list), a path bounds them to it (the dashboard passes
    the workspace, so it never shows strays from elsewhere on the machine).
    """
    if scope.project is not None and scope.goal is not None:
        return [a for a in listing if in_goal(a.cwd, scope.project.worktrees, scope.goal)]
    if scope.project is not None:
        return [a for a in listing if under(a.cwd, scope.project.dir)]
    if otherwise is None:
        return listing
    return [a for a in listing if under(a.cwd, otherwise)]


def under(path: Path, root: Path) -> bool:
    """Whether path is root or a descendant of it (both resolved)."""
    path, root = path.resolve(), root.resolve()
    return path == root or root in path.parents


def in_goal(cwd: Path, worktrees: Path, goal: str) -> bool:
    """Whether cwd sits in one of goal's actor worktrees (``<goal>@<actor>``) under worktrees."""
    worktrees = worktrees.resolve()
    if not under(cwd, worktrees):
        return False
    relative = cwd.resolve().relative_to(worktrees)
    return bool(relative.parts) and relative.parts[0].startswith(f'{goal}{SEP}')


# Transcript metadata records Claude resolves a session label from, mapping each
# record `type` to the field carrying its value — highest precedence first.
TITLES = {'custom-title': 'customTitle', 'ai-title': 'aiTitle', 'last-prompt': 'lastPrompt'}


def session_summary(cwd: str, name: str, projects: Path | None = None) -> str | None:
    """A one-line summary of the live session in ``cwd``: its title or last prompt.

    Claude stores each session's transcript under a per-cwd folder of ``projects``
    (``~/.claude/projects`` by default). The live session's reported id rarely names
    its transcript (background agents resume under fresh ids), so we read the newest
    transcript in that folder — the one currently being appended to — and mirror
    Claude's own precedence: a user-set title, then an AI-generated topic, then the
    last prompt. Anything equal to ``name`` is skipped — Claude persists ``--name``
    as a title, so it would merely echo what we already show. ``None`` when nothing
    distinct remains.
    """
    projects = projects if projects is not None else Path.home() / '.claude' / 'projects'
    folder = projects / re.sub(r'[^a-zA-Z0-9]', '-', cwd)
    transcripts = sorted(folder.glob('*.jsonl'), key=lambda p: p.stat().st_mtime, reverse=True)
    latest: dict[str, str] = {}
    for line in reversed(transcripts[0].read_text().splitlines()) if transcripts else ():
        if not line.strip():
            continue
        record = json.loads(line)
        field = TITLES.get(str(record.get('type')))
        value = record.get(field) if field else None  # a typed record may omit its value
        if field and value and field not in latest:  # reversed, so first seen is the file's latest
            latest[field] = ' '.join(str(value).split())
            if len(latest) == len(TITLES):
                break
    return next((latest[f] for f in TITLES.values() if f in latest and latest[f] != name), None)


def agent(
    worktree: Path,
    name: str,
    prompt: str | None = None,
    extra: Sequence[str] = (),
    dangerous: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    """Run a claude agent named <name>, with cwd set to the worktree.

    Runs interactively in the foreground unless ``prompt`` is given, in which case
    it daemonizes (`claude --bg`) to work on the prompt autonomously. ``extra`` is
    passed straight through to ``claude`` (e.g. ``--model``, ``--dangerously-skip-permissions``).
    ``dangerous`` makes bypass-permissions mode reachable (see ``_session_args``).
    Refuses if a claude session is already live in the worktree.
    """
    return _launch(worktree, _session_args(['--name', name], prompt, extra, dangerous))


def resume(
    worktree: Path,
    name: str,
    prompt: str | None = None,
    extra: Sequence[str] = (),
    dangerous: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    """Resume the claude session named <name>, with cwd set to the worktree.

    The inverse of ``agent``: where ``agent`` launches a session with ``--name <name>``,
    ``resume`` reattaches to that same label with ``--resume <name>``. The cwd is the
    key — claude has no ``--cwd``, so setting it here is what lets a session be revived
    in its worktree from anywhere. Interactive foreground by default; with ``prompt`` it
    resumes in the background (``--bg``) to keep working. ``extra`` passes straight
    through to ``claude``. ``dangerous`` makes bypass-permissions mode reachable (see
    ``_session_args``). Refuses if a claude session is already live in the worktree.
    """
    return _launch(worktree, _session_args(['--resume', name], prompt, extra, dangerous))


# claude only makes bypass-permissions mode reachable via shift-tab when launched with this;
# availability is fixed at launch, so it must ride on the very command that starts the session.
ALLOW_BYPASS = '--allow-dangerously-skip-permissions'

# the forms that already arrange for bypass mode — don't double up if the caller passed one
_BYPASS_FLAGS = frozenset({ALLOW_BYPASS, '--dangerously-skip-permissions'})


def _session_args(
    lead: list[str], prompt: str | None, extra: Sequence[str], dangerous: bool
) -> list[str]:
    """The claude argv tail: ``--bg`` when backgrounding, the lead, passthrough, then prompt.

    With ``dangerous`` the session also gets ``--allow-dangerously-skip-permissions`` (unless
    ``extra`` already asks for bypass) so bypass-permissions mode is reachable with shift-tab.
    It's opt-in: enabling bypass *displaces* auto-accept from claude's shift-tab cycle, so the
    everyday default keeps auto-accept and only an explicit request pays that cost. A ``--bg``
    session is an attachable fork, not headless — you cycle after attaching — and the mode's
    availability is decided at *its* launch, so the flag has to ride the background launch too.
    The flag only enables the mode; the autonomous run keeps its resolved mode.
    """
    allow = (ALLOW_BYPASS,) if dangerous and not _BYPASS_FLAGS.intersection(extra) else ()
    if prompt is not None:
        return ['--bg', *lead, *extra, *allow, prompt]
    return [*lead, *extra, *allow]


def _launch(worktree: Path, args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    """Run ``claude <args>`` with cwd set to the worktree; refuse if one is already live."""
    if not worktree.is_dir():
        raise FileNotFoundError(worktree)
    if running := live_sessions(worktree):
        ids = ', '.join(f'{s["sessionId"]} ({s["status"]})' for s in running)
        raise RuntimeError(f'an agent is already live in {worktree}: {ids} — attach or stop it')
    return subprocess.run(['claude', *args], cwd=worktree, check=True)


def live_sessions(worktree: Path) -> list[dict[str, object]]:
    """Claude sessions verified still live (pid actually running) under the worktree."""
    return _sessions('--cwd', str(worktree))


def all_sessions() -> list[dict[str, object]]:
    """Claude sessions verified still live (pid actually running) anywhere."""
    return _sessions()


def _sessions(*scope: str) -> list[dict[str, object]]:
    result = subprocess.run(
        ['claude', 'agents', '--json', *scope],
        capture_output=True,
        text=True,
        check=True,
    )
    return [session for session in json.loads(result.stdout) if _verify_live(session)]


def _verify_live(session: dict[str, object]) -> bool:
    """Whether session's reported pid names a process that's actually still running.

    Claude's own background-agent registry (``claude agents --json``) can report a
    stale, degraded entry for some time after its process has already died — pid
    and status may be missing entirely (observed after killing a backgrounded agent
    directly by pid) — before it's eventually pruned. Rather than trust an entry's
    mere presence, re-verify: ``os.kill(pid, 0)`` sends no signal, only probes.
    A missing/non-int pid can't be probed, so it's treated as stale like a dead one;
    either case is logged (with the full session) for anyone debugging a liveness
    check that looked wrong.
    """
    pid = session.get('pid')
    if not isinstance(pid, int):
        logger.bind(session=session).warning('agent: session has no pid, treating as stale')
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        logger.bind(session=session).warning('agent: session pid is dead, treating as stale')
        return False
    except PermissionError:
        logger.bind(session=session).info('agent: session pid owned by another user')
    return True
