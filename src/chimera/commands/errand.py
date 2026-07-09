"""One-shot, read-only agent dispatch into a foreign project — ``ch errand``.

The review pattern (external thing → ephemeral goal via the worktree machinery →
guardrailed prompt → launch), compressed to one synchronous command: the goal is
generated (``errand-<6hex>``), the harness runs headless (``Agent.run``, read-only),
chimera itself delivers the report (``out`` or the caller's stdout), and the goal is
swept back down through ``worktree rm``'s safety checks.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from secrets import token_hex

from loguru import logger

from chimera.agents.registry import AgentSpec
from chimera.commands.agent import refuse_restricted
from chimera.commands.worktree.add import add
from chimera.commands.worktree.rm import remove
from chimera.config import UserError
from chimera.dry import Dry
from chimera.git import Git
from chimera.worktrees import AGENT, branch, goals, session_name, worktree_path

# Prepended to every errand prompt. Affirmative — identity and the report contract,
# never a prohibition list: what an errand must not do is enforced by the read-only
# harness wall (``Agent.run(readonly=True)``), not prose.
GUARDRAIL = (
    'ERRAND: you are a one-shot research agent, dispatched into this repository to '
    'answer the request below. Your final message must be the complete report in '
    'markdown, nothing else — it is captured verbatim.\n\n'
)


@dataclass(frozen=True)
class ErrandResult:
    """What an errand produced: its ephemeral goal, where it ran, and the report."""

    goal: str
    worktree: Path
    out: Path | None  # where the report was written; None = the caller's to print
    report: str
    cleaned: bool  # False: the goal still stands (kept, or the teardown refused)


def errand(
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
    """Dispatch a one-shot read-only agent into ``target``'s ``repo``; deliver its report.

    Stands up an ephemeral goal (``errand-<6hex>`` — fresh against the existing
    worktrees, refs logged on ``errand: refs``), runs ``spec``'s harness headless in
    its worktree on the guardrailed prompt, then hands the report off: written to
    ``out`` when given (logged with path/bytes/sha256 on ``errand: result`` — the
    audit twin of ``context: rendered``), else returned for the caller to print.
    ``context``/``env`` are factories keyed by the session name, as on ``review`` —
    only this function knows the generated goal.

    Unless ``keep``, the goal is then swept through ``worktree rm``'s safety checks:
    a clean, trivially merged errand vanishes; a refusal (work left behind) is warned
    about and reported via ``cleaned=False``, never an errand failure — the report
    was already delivered. A failed run still attempts the sweep, then re-raises.
    A ``target`` with no repo checkout (a reference project) refuses up front.
    Every mutation routes through ``dry``, so a dry run resolves everything —
    including the goal id — but creates, runs and removes nothing.
    """
    refuse_restricted(spec, extra)
    if not repo.is_dir():
        raise UserError(f"project '{target}' has no repo checkout at {repo} to dispatch into")
    goal = _fresh_goal(worktrees_root)
    worktree = worktree_path(worktrees_root, goal, AGENT)
    git = Git(repo)
    with git.ref_log('errand: refs', branch(goal, AGENT), goal=goal, worktree=str(worktree)):
        dry(add, repo, worktrees_root, goal=goal, frm=frm, fetch=fetch)
    name = session_name(target, goal, AGENT)
    rendered = context(name) if context is not None else None
    stamp = env(name) if env is not None else {}
    report = ''

    def _run() -> None:
        nonlocal report
        report = spec.agent.run(
            worktree,
            name,
            GUARDRAIL + prompt,
            extra,
            model=spec.model,
            context=rendered,
            env=stamp,
            readonly=True,
            timeout=timeout,
        )

    try:
        dry(_run)
    except Exception:
        # the report is lost but the goal needn't be leaked: best-effort teardown,
        # with the run's own failure staying the error that propagates
        if not keep:
            _finish(repo, worktrees_root, goal, dry)
        raise
    if out is not None:
        dry(_write, name, out, report)
    cleaned = False if keep else _finish(repo, worktrees_root, goal, dry)
    return ErrandResult(goal=goal, worktree=worktree, out=out, report=report, cleaned=cleaned)


def _fresh_goal(worktrees_root: Path) -> str:
    """An ``errand-<6hex>`` goal no existing worktree already uses.

    Valid by construction (hex satisfies the goal-name grammar), and fresh by retry
    against :func:`~chimera.worktrees.goals`, so a collision — however unlikely —
    can never adopt a live goal's branches.
    """
    existing = goals(worktrees_root)
    while (goal := f'errand-{token_hex(3)}') in existing:
        pass
    return goal


def _write(session: str, out: Path, report: str) -> None:
    """Write the report to ``out``, landing its audit line — path, size and hash, so
    the log alone proves exactly what was delivered."""
    data = report.encode()
    out.write_bytes(data)
    logger.bind(
        session=session, path=str(out), bytes=len(data), sha256=sha256(data).hexdigest()
    ).info('errand: result')


def _finish(repo: Path, worktrees_root: Path, goal: str, dry: Dry) -> bool:
    """Sweep the errand's goal through ``worktree rm``'s safety checks; False on refusal.

    A clean, trivially merged errand disappears; one that left work behind trips the
    usual dirty/unmerged refusal — warned about here (the caller reports it) and left
    standing as an ordinary goal for ``ch goal finish``.
    """
    try:
        remove(repo, worktrees_root, goal, fetch=False, dry=dry)
    except RuntimeError as error:
        logger.bind(goal=goal, refusal=str(error)).warning('errand: cleanup refused')
        return False
    return True
