import json
import subprocess
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from string import Template
from urllib.parse import urlsplit

from giterator import Git, GitError
from loguru import logger

from chimera.commands.agent import agent
from chimera.commands.worktree.add import add
from chimera.config import UserError
from chimera.worktrees import (
    ACTORS,
    AGENT,
    HUMAN,
    branch,
    checkout_here,
    ref_shas,
    registered_worktrees,
    session_name,
    worktree_path,
)

# Prepended to every review prompt, override or default alike, so a project's own
# prompts/review.md can shape *what* to review but never license *publishing* it.
GUARDRAIL = (
    'PRE-HUMAN REVIEW: produce findings for the human reviewer only. Do NOT post anything to '
    'the pull request — no comments, no reviews, no `gh` write commands, no '
    "`/code-review --comment`. Publishing is the human's decision.\n\n"
)

# The JSON gh resolves for us; headRefOid is the authoritative head commit (see _wire_upstream).
_PR_FIELDS = 'number,headRefOid,baseRefName,title,url,isCrossRepository,state,headRepositoryOwner'


def review(
    repo: Path,
    worktrees_root: Path,
    project: str,
    prompts_dir: Path,
    pr: str,
    extra: Sequence[str] = (),
    dangerous: bool = False,
    into: Path | None = None,
) -> Path:
    """Stand a goal up from pull request ``pr`` (number or URL) and launch a review agent.

    Resolves the PR through ``gh`` (authoritative ``headRefOid``), wires ``refs/pull/<N>/head``
    in as the ``origin/pr/<N>`` tracking ref, branches ``<goal>/{human,agent}`` off the verified
    head with that ref as upstream, and launches the agent on a review prompt — the project's
    ``prompts/review.md`` if present, else the packaged default, both behind a no-publish
    guardrail. ``into`` optionally lands the human branch in place (see ``checkout_here``).

    Idempotent: an existing ``<goal>@agent`` worktree is reused, so a re-run only relaunches.
    The goal's branches are logged before/after creation (see ``agent-docs/logging.md``).
    Returns the agent worktree.
    """
    git = Git(repo)
    meta = _pr_metadata(repo, pr)
    _check_pr_repo(git, meta['url'], project)
    number, head_oid = int(str(meta['number'])), str(meta['headRefOid'])
    goal = f'pr-{number}'
    tracking = _wire_upstream(git, number, head_oid)
    before = _goal_refs(git, goal)
    agent_worktree = _ensure_goal(git, repo, worktrees_root, goal, head_oid, tracking)
    logger.bind(
        goal=goal,
        git={'before': before, 'after': _goal_refs(git, goal)},
        worktree=str(agent_worktree),
    ).info('review: refs')
    if into is not None:
        checkout_here(git, branch(goal, HUMAN), into, 'review')
    prompt = _prompt(prompts_dir, meta, goal, project)
    agent(agent_worktree, session_name(project, goal, AGENT), prompt, extra, dangerous)
    return agent_worktree


def _pr_metadata(repo: Path, pr: str) -> dict[str, object]:
    """The PR's metadata from ``gh pr view`` (``pr`` may be a number or a URL)."""
    result = subprocess.run(
        ['gh', 'pr', 'view', pr, '--json', _PR_FIELDS],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise UserError(f'gh pr view {pr} failed: {result.stderr.strip()}')
    return json.loads(result.stdout)


def _check_pr_repo(git: Git, url: object, project: str) -> None:
    """Refuse when the PR's repo isn't the project's origin — a URL pointing at a different repo.

    ``ch review`` resolves the project from cwd but takes git refs from *that* project's repo, so
    a URL for another repo would try to fetch a PR ref this origin doesn't have (poisoning nothing
    now, but still failing against the wrong repo). When both the PR URL and the origin carry a
    github identity, a mismatch is refused up front with a clear message instead of a confusing
    fetch failure. Skipped when either side has no comparable identity — a local-path origin, or a
    number-only PR argument whose metadata URL doesn't parse.
    """
    try:
        origin = git('remote', 'get-url', 'origin').strip()
    except GitError:
        return  # no origin to compare against — the fetch itself will surface any problem
    if (
        (want := _repo_slug(urlsplit(str(url)).path))
        and (have := _origin_slug(origin))
        and want != have
    ):
        raise UserError(
            f"PR is on {want}, but project '{project}' tracks {have} — "
            f'run ch review from the project tracking {want} (or pass -p).'
        )


def _repo_slug(path: str) -> str:
    """``owner/repo`` (lowercased) from a URL path like ``/owner/repo/pull/<n>``; '' if too short."""
    parts = path.strip('/').split('/')
    return '/'.join(parts[:2]).lower() if len(parts) >= 2 else ''


def _origin_slug(url: str) -> str:
    """``owner/repo`` from a github-style origin (https/ssh/scp); '' for a bare local path."""
    text = url.removesuffix('.git')
    if '://' in text:
        return _repo_slug(urlsplit(text).path)
    if ':' in text and '/' not in text.split(':', 1)[0]:  # scp-like git@host:owner/repo
        return _repo_slug(text.split(':', 1)[1])
    return ''  # a local-path origin has no remote identity to compare


def _wire_upstream(git: Git, number: int, head_oid: str) -> str:
    """Fetch ``refs/pull/<number>/head`` as ``origin/pr/<number>`` and verify it is ``head_oid``.

    Fetches the PR head into the tracking ref *first* — a targeted fetch that touches no config —
    then persists the fetch refspec (once, idempotent) so the PR head stays a real remote-tracking
    ref ``git status`` compares against on later fetches. Persisting only *after* a clean fetch is
    the crash-safety: a missing or foreign PR ref (the fetch fails) can't leave a dead refspec in
    ``remote.origin.fetch`` that bricks every future ``git fetch`` in the repo. Trusts gh's
    ``headRefOid`` as the source of truth — a mismatch means a stale fetch and is refused. Returns
    the tracking ref name.
    """
    tracking = f'origin/pr/{number}'
    spec = f'+refs/pull/{number}/head:refs/remotes/{tracking}'
    git('fetch', 'origin', spec)  # validate + create the ref without mutating config on failure
    try:
        existing = git('config', '--get-all', 'remote.origin.fetch').splitlines()
    except GitError:
        existing = []  # no fetch refspec configured yet
    if spec not in existing:
        git('config', '--add', 'remote.origin.fetch', spec)
    if (fetched := git.rev_parse(tracking, short=False)) != head_oid:
        raise UserError(
            f'PR #{number}: fetched {tracking} is {fetched}, but gh reports headRefOid {head_oid}'
        )
    return tracking


def _ensure_goal(
    git: Git, repo: Path, worktrees_root: Path, goal: str, head_oid: str, tracking: str
) -> Path:
    """Create ``<goal>/{human,agent}`` at ``head_oid`` tracking ``tracking``; reuse if present."""
    agent_worktree = worktree_path(worktrees_root, goal, AGENT)
    if agent_worktree.resolve() not in registered_worktrees(git):
        add(repo, worktrees_root, goal=goal, actors=ACTORS, frm=head_oid, fetch=False)
        for actor in ACTORS:
            git('branch', f'--set-upstream-to={tracking}', branch(goal, actor))
    return agent_worktree


def _goal_refs(git: Git, goal: str) -> dict[str, str]:
    """The goal's actor branches that exist, each mapped to its full sha (for logging)."""
    return ref_shas(git, branch(goal, HUMAN), branch(goal, AGENT))


def _prompt(prompts_dir: Path, meta: dict[str, object], goal: str, project: str) -> str:
    """The review prompt: the no-publish guardrail plus a rendered template.

    Uses the project's ``prompts/review.md`` when present, else the packaged default. Rendered
    with :class:`string.Template` (``$VAR``; unknown ``$`` left intact), so a template is plain
    text with a handful of holes — never a logic language.
    """
    override = prompts_dir / 'review.md'
    text = override.read_text() if override.exists() else _default_template()
    return GUARDRAIL + Template(text).safe_substitute(
        PR=meta['number'],
        PR_URL=meta['url'],
        PR_TITLE=meta['title'],
        BASE=meta['baseRefName'],
        GOAL=goal,
        PROJECT=project,
    )


def _default_template() -> str:
    """The packaged fallback review template, shipped as ``chimera`` package data."""
    return (files('chimera.prompts') / 'review.md').read_text()
