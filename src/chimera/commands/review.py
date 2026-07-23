import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from string import Template
from urllib.parse import urlsplit

from giterator import GitError
from loguru import logger

from chimera.agents.registry import AgentSpec
from chimera.commands.agent import agent
from chimera.commands.prompt import REVIEW_STEP, resolve
from chimera.commands.worktree.add import add
from chimera.config import UserError
from chimera.dry import Dry
from chimera.git import Git, remote_slug, repo_slug, sibling_url
from chimera.addresses import Actor
from chimera.worktrees import (
    ACTORS,
    AGENT,
    HUMAN,
    branch,
    checkout_here,
    registered_worktrees,
    worktree_path,
)

# Prepended to every review prompt, override or default alike, so a project's own
# prompts/review.md can shape *what* to review but never license *publishing* it.
GUARDRAIL = (
    'PRE-HUMAN REVIEW: produce findings for the human reviewer only. Do NOT post anything to '
    'the pull request — no comments, no reviews, no `gh` write commands, no '
    "`/code-review --comment`. Publishing is the human's decision.\n\n"
)

# The JSON gh resolves for us; headRefOid is the authoritative head commit (see _fetch_head).
_PR_FIELDS = (
    'number,headRefOid,headRefName,baseRefName,title,url,'
    'isCrossRepository,maintainerCanModify,state,headRepository,headRepositoryOwner'
)

# gh viewerPermission values that carry push access — what a maintainer-edit push requires.
_PUSH_PERMISSIONS = frozenset({'ADMIN', 'MAINTAIN', 'WRITE'})


def review(
    repo: Path,
    worktrees_root: Path,
    project: str,
    prompts_dir: Path,
    pr: str,
    extra: Sequence[str] = (),
    dangerous: bool = False,
    into: Path | None = None,
    launch: bool = True,
    review_step: str | None = None,
    spec: AgentSpec = AgentSpec(),
    context: Callable[[str, str], Path | None] | None = None,
    env: Callable[[str, str], Mapping[str, str]] | None = None,
    dry: Dry = Dry(),
) -> Path:
    """Stand a goal up from pull request ``pr`` (number or URL) and launch a review agent.

    Resolves the PR through ``gh`` (authoritative ``headRefOid``), wires a tracking ref the
    goal's branches can push back to the PR through where possible (see ``_wire_tracking`` —
    the PR's real branch on origin or its fork, falling back to the read-only
    ``refs/pull/<N>/head``), branches ``<goal>/{human,agent}`` off the verified head with that
    ref as upstream, and launches the agent on a review prompt — the project's
    ``prompts/review.md`` if present, else the packaged default, both behind a no-publish
    guardrail. ``into`` optionally lands the human branch in place (see ``checkout_here``).

    ``review_step`` fills the template's ``$REVIEW`` hole (default: :data:`REVIEW_STEP`) —
    the per-run knob for *how* the diff gets reviewed, leaving the surrounding template
    alone. A template with no such hole refuses it rather than dropping it silently.

    ``context`` is a factory keyed by ``(session name, goal)`` — called with the
    *resolved* ``<project>@pr-<N>@agent`` and ``pr-<N>`` — the number is only known
    here, once ``gh`` has resolved ``pr``, so a URL argument still lands its context
    artifact (and the ``context: rendered`` log line) under the real session name.
    Never called without ``launch``: no session, nothing to render for. ``env`` — the
    role stamp overlaid on the session's environment — is a factory keyed the same
    way, for the same reason: its scope carries a goal only this function resolves.

    ``launch=False`` (CLI ``--no-agent``) stops after the checkout: branches, worktree and
    upstream all stand, but no agent runs — kick one off later with ``ch agent start``. The
    agent-only knobs (``dangerous``, ``extra``) are refused with it: nothing would read them.

    Idempotent: an existing ``<goal>@agent`` worktree is reused, so a re-run only relaunches.
    The goal's branches are logged before/after creation (see ``agent-docs/logging.md``) —
    quiet when nothing changed, so a re-run or ``dry`` lands no ref line.
    Returns the agent worktree.
    """
    if not launch and (dangerous or extra or review_step is not None):
        raise UserError(
            '--no-agent launches no agent, so --dangerous, --review and "-- …" have '
            'nothing to apply to.'
        )
    git = Git(repo)
    if 'origin' not in git('remote').split():
        raise UserError(
            f"project '{project}' has no origin to fetch a PR from — "
            f'publish it first: ch project push <url>'
        )
    meta = _pr_metadata(repo, _pr_argument(git, pr, project))
    _check_pr_repo(git, meta['url'], project)
    number, head_oid = int(str(meta['number'])), str(meta['headRefOid'])
    goal = f'pr-{number}'
    tracking = _wire_tracking(git, repo, meta, dry)
    agent_worktree = worktree_path(worktrees_root, goal, AGENT)
    with git.ref_log(
        'review: refs',
        branch(goal, HUMAN),
        branch(goal, AGENT),
        goal=goal,
        worktree=str(agent_worktree),
    ):
        dry(_ensure_goal, git, repo, worktrees_root, goal, head_oid, tracking)
    if into is not None:
        dry(checkout_here, git, branch(goal, HUMAN), into, 'review')
    if launch:
        name = str(Actor(project, goal, AGENT))
        prompt = _prompt(prompts_dir, meta, goal, project, review_step)
        agent(
            agent_worktree,
            name,
            prompt,
            extra,
            dangerous,
            spec,
            context(name, goal) if context is not None else None,
            env(name, goal) if env is not None else {},
            dry,
        )
    return agent_worktree


def _pr_argument(git: Git, pr: str, project: str) -> str:
    """The argument to hand ``gh``: ``pr`` itself, or the number dug out of a review-tool URL.

    ``gh`` understands numbers, branches and github URLs, so those pass straight through
    (``_check_pr_repo`` still refuses a github URL for a foreign repo). Any other URL —
    reviewable.io, graphite, … — embeds the same ``owner/repo`` and PR number somewhere in its
    path, and the project's origin already names the repo: finding the origin's slug in the path
    and taking the first numeric segment after it recovers the number generically, no per-tool
    table. A URL that doesn't name the origin's repo is refused up front — stripping its number
    anyway would silently review a different repo's PR of the same number.
    """
    if '://' not in pr:
        return pr  # a number or branch: gh's to interpret
    host = urlsplit(pr).hostname or ''
    if host == 'github.com' or host.endswith('.github.com'):
        return pr
    try:
        origin = git('remote', 'get-url', 'origin').strip()
    except GitError:
        origin = ''
    if not (have := remote_slug(origin)):
        raise UserError(
            f"project '{project}' has no github origin to match {pr} against — "
            f'pass the PR number instead.'
        )
    if number := _number_after_slug(urlsplit(pr).path, have):
        logger.bind(url=pr, number=number).info('review: pr number from url')
        return number
    raise UserError(
        f"{pr} doesn't name a PR of {have}, which project '{project}' tracks — "
        f'pass the PR number, or run ch review from the project tracking it (or pass -p).'
    )


def _number_after_slug(path: str, slug: str) -> str | None:
    """The first numeric path segment after an adjacent ``owner/repo`` pair matching ``slug``."""
    segments = path.strip('/').split('/')
    lowered = [s.lower() for s in segments]
    owner, repo = slug.split('/')
    for i in range(len(segments) - 1):
        if (lowered[i], lowered[i + 1]) == (owner, repo):
            for segment in segments[i + 2 :]:
                if segment.isdigit():
                    return segment
    return None


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
        (want := repo_slug(urlsplit(str(url)).path))
        and (have := remote_slug(origin))
        and want != have
    ):
        raise UserError(
            f"PR is on {want}, but project '{project}' tracks {have} — "
            f'run ch review from the project tracking {want} (or pass -p).'
        )


def _wire_tracking(git: Git, repo: Path, meta: dict[str, object], dry: Dry) -> str:
    """The tracking ref for the goal's branches: the PR's real branch when pushable, else its ref.

    Picks, in order: origin's own head branch (a same-repo PR — write access to origin is write
    access to the PR); the fork's head branch when the PR grants maintainer edits and the viewer
    has write on origin (the fork lands as a named remote — see ``_wire_fork``); else the
    read-only ``refs/pull/<N>/head``, which always exists and outlives a deleted fork or branch.
    A branch tracking the real head is one a human can push back to the PR from — the name
    mismatch (``pr-<N>/human`` vs the head branch) still makes bare ``git push`` refuse, but
    git's refusal prints the exact ``git push <remote> HEAD:<branch>`` to run, instead of
    silently minting a junk branch. Every choice lands a ``review: tracking`` line, at WARNING
    when it's a degraded fallback.

    The decision itself is read-only (``git ls-remote`` probes, never a fetch) and always runs
    for real, dry or not — only the fetch/config that *acts* on the decision routes through
    ``dry`` — so a preview names the exact same tracking ref the real run would wire, never an
    optimistic guess a real run could then contradict.
    """
    number, head_ref = int(str(meta['number'])), str(meta['headRefName'])
    head_oid, pr_ref = str(meta['headRefOid']), f'origin/pr/{number}'

    def decided(tracking: str, reason: str, degraded: bool = False) -> str:
        line = logger.bind(tracking=tracking, reason=reason)
        (line.warning if degraded else line.info)('review: tracking')
        return tracking

    def fallback(reason: str, degraded: bool = False) -> str:
        dry(_wire_pr_ref, git, number, head_oid, pr_ref)
        return decided(pr_ref, reason, degraded)

    if not meta['isCrossRepository']:
        if _remote_head_oid(git, 'origin', head_ref) != head_oid:
            return fallback('head branch not on origin', degraded=True)
        dry(_fetch_head, git, 'origin', head_ref, f'origin/{head_ref}', head_oid, number)
        return decided(f'origin/{head_ref}', 'same-repo head branch')
    if not meta['maintainerCanModify']:
        return fallback('fork PR without maintainer edits')
    if not _viewer_can_push(repo):
        return fallback('no write access to origin')
    login = _nested(meta, 'headRepositoryOwner', 'login')
    name = _nested(meta, 'headRepository', 'name')
    slug = f'{login}/{name}'
    url = sibling_url(git('remote', 'get-url', 'origin').strip(), slug) if login and name else ''
    if not url:
        return fallback('fork identity unknown', degraded=True)
    if _remote_head_oid(git, url, head_ref) != head_oid:
        return fallback('fork branch not reachable at the expected head', degraded=True)
    _check_remote(git, login, url, slug)  # a colliding remote refuses, under --dry too
    dry(_wire_fork, git, login, url, head_ref, f'{login}/{head_ref}', head_oid, number)
    return decided(f'{login}/{head_ref}', 'maintainer-editable fork')


def _remote_head_oid(git: Git, source: str, head_ref: str) -> str | None:
    """The sha ``source`` has at ``refs/heads/<head_ref>``, or ``None`` if absent/unreachable.

    A read-only probe (``git ls-remote``, by name or URL) that never mutates anything — the
    discovery step ``_wire_tracking`` needs to decide the tracking ref the same way whether or
    not this is a dry run.
    """
    try:
        output = git('ls-remote', source, f'refs/heads/{head_ref}').strip()
    except GitError:
        return None
    return output.split('\t', 1)[0] if output else None


def _nested(meta: dict[str, object], key: str, field: str) -> str:
    """``meta[key][field]`` as a string; '' when the object is missing (a deleted fork)."""
    value = meta.get(key)
    return str(value.get(field) or '') if isinstance(value, dict) else ''


def _viewer_can_push(repo: Path) -> bool:
    """Whether the gh viewer has write access to origin — what a maintainer-edit push requires.

    Failing to ask (gh offline, auth) degrades to False with a warning: the read-only PR ref
    still reviews fine, while a fork branch wired as pushable would just promise a push the
    server will refuse.
    """
    result = subprocess.run(
        ['gh', 'repo', 'view', '--json', 'viewerPermission'],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.bind(stderr=result.stderr.strip()).warning('review: viewer permission unknown')
        return False
    return str(json.loads(result.stdout).get('viewerPermission')) in _PUSH_PERMISSIONS


def _check_remote(git: Git, remote: str, url: str, slug: str) -> None:
    """Refuse when ``remote`` already exists but names something other than the fork.

    An existing remote for the fork — accreted by a previous PR from the same contributor —
    is fine whatever URL shape it uses (ssh where we derived https, say): the slugs match.
    """
    if remote not in git('remote').split():
        return
    existing = git('remote', 'get-url', remote).strip()
    if existing != url and remote_slug(existing) != slug.lower():
        raise UserError(
            f"remote '{remote}' already points at {existing}, not {slug} — "
            f'rename or remove it first'
        )


def _wire_fork(
    git: Git, remote: str, url: str, head_ref: str, tracking: str, head_oid: str, number: int
) -> None:
    """Fetch the fork's head branch as ``tracking`` and, once that succeeds, persist ``remote``.

    Fetching by URL before the remote exists is the crash-safety (``_wire_pr_ref``'s pattern):
    a dead or foreign fork never lands a remote pointing at it. The remote then accretes
    deliberately — never swept, it keeps paying off on the contributor's next PR (an existing
    one has already passed ``_check_remote``, so it's fetched by name and left untouched).
    """
    known = remote in git('remote').split()
    _fetch_head(git, remote if known else url, head_ref, tracking, head_oid, number)
    if not known:
        git('remote', 'add', remote, url)
        logger.bind(remote=remote, url=url).info('review: remote add')


def _fetch_head(
    git: Git, source: str, head_ref: str, tracking: str, head_oid: str, number: int
) -> None:
    """Fetch ``refs/heads/<head_ref>`` from ``source`` as ``tracking``, verified as ``head_oid``.

    Trusts gh's ``headRefOid`` as the source of truth — a mismatch means a stale fetch (or a
    PR that moved mid-command) and is refused; a re-run resolves either.
    """
    git('fetch', source, f'+refs/heads/{head_ref}:refs/remotes/{tracking}')
    _verify(git, number, tracking, head_oid)


def _verify(git: Git, number: int, tracking: str, head_oid: str) -> None:
    if (fetched := git.rev_parse(tracking, short=False)) != head_oid:
        raise UserError(
            f'PR #{number}: fetched {tracking} is {fetched}, but gh reports headRefOid {head_oid}'
        )


def _wire_pr_ref(git: Git, number: int, head_oid: str, tracking: str) -> str:
    """Fetch ``refs/pull/<number>/head`` as ``tracking`` and verify it is ``head_oid``.

    Fetches the PR head into the tracking ref *first* — a targeted fetch that touches no config —
    then persists the fetch refspec (once, idempotent) so the PR head stays a real remote-tracking
    ref ``git status`` compares against on later fetches. Persisting only *after* a clean fetch is
    the crash-safety: a missing or foreign PR ref (the fetch fails) can't leave a dead refspec in
    ``remote.origin.fetch`` that bricks every future ``git fetch`` in the repo. Returns the
    tracking ref name.
    """
    spec = f'+refs/pull/{number}/head:refs/remotes/{tracking}'
    git('fetch', 'origin', spec)  # validate + create the ref without mutating config on failure
    try:
        existing = git('config', '--get-all', 'remote.origin.fetch').splitlines()
    except GitError:
        existing = []  # no fetch refspec configured yet
    if spec not in existing:
        git('config', '--add', 'remote.origin.fetch', spec)
    _verify(git, number, tracking, head_oid)
    return tracking


def _ensure_goal(
    git: Git, repo: Path, worktrees_root: Path, goal: str, head_oid: str, tracking: str
) -> None:
    """Create ``<goal>/{human,agent}`` at ``head_oid`` tracking ``tracking``; reuse if present."""
    if worktree_path(worktrees_root, goal, AGENT).resolve() not in registered_worktrees(git):
        add(repo, worktrees_root, goal=goal, actors=ACTORS, frm=head_oid, fetch=False)
        for actor in ACTORS:
            git('branch', f'--set-upstream-to={tracking}', branch(goal, actor))


def _prompt(
    prompts_dir: Path,
    meta: dict[str, object],
    goal: str,
    project: str,
    review_step: str | None = None,
) -> str:
    """The review prompt: the no-publish guardrail plus a rendered template.

    Uses the project's ``prompts/review.md`` when present, else the packaged default (see
    :func:`chimera.commands.prompt.resolve`). Rendered with :class:`string.Template` (``$VAR``;
    unknown ``$`` left intact), so a template is plain text with a handful of holes — never a
    logic language.

    An explicit ``review_step`` against a template with no ``$REVIEW`` hole is refused rather
    than dropped: a flag that silently does nothing is the dead end the ``--dry`` preview and
    this refusal both exist to prevent. A template predating the hole is untouched otherwise.
    """
    template = resolve(prompts_dir, 'review')
    text = template.text
    if review_step is not None and 'REVIEW' not in Template(text).get_identifiers():
        raise UserError(
            f'{template.source} has no $REVIEW hole for --review to fill — '
            f'ch prompt show review prints it, ch prompt init review copies the default'
        )
    return GUARDRAIL + Template(text).safe_substitute(
        PR=meta['number'],
        PR_URL=meta['url'],
        PR_TITLE=meta['title'],
        BASE=meta['baseRefName'],
        GOAL=goal,
        PROJECT=project,
        REVIEW=review_step if review_step is not None else REVIEW_STEP,
    )
