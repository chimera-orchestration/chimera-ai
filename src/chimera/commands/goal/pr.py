import json
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from string import Template

from loguru import logger

from chimera.commands.goal.merge import source_branch
from chimera.config import UserError
from chimera.dry import Dry
from chimera.git import Git
from chimera.worktrees import (
    default_branch,
    fetch_origin_or_offline,
    goal_branch_actors,
)

# The model `_compose` asks to write a multi-commit branch's PR description from a
# project-customisable prompt (see that function). Kept lightweight, as doctor's
# workspace-clean commit writer is — the template carries the judgement.
_COMPOSE_MODEL = 'haiku'


@dataclass(frozen=True)
class PrResult:
    """What proposing the goal did: what was pushed where, and the PR it landed in."""

    source: str  # the actor branch whose tip was pushed
    remote_branch: str  # the branch name on origin (the goal name)
    base: str  # the branch the PR targets
    sha: str  # the pushed tip (short)
    title: str  # with body, '' when composing was skipped: an open PR already has its own
    body: str
    url: str | None  # the PR's URL; None only under dry when none is open yet
    created: bool  # False when a PR for the branch was already open (push still updates it)


def pr(
    repo: Path,
    project: str,
    prompts_dir: Path,
    goal: str,
    into: str | None = None,
    draft: bool = False,
    fetch: bool = True,
    dry: Dry = Dry(),
) -> PrResult:
    """Publish a finished goal as a pull request, leaving every local branch standing.

    The remote-review sibling of ``goal merge``: the same source selection (the actor
    branch containing every other actor's work — :func:`source_branch`; diverged actors
    refuse, pointing at ``goal sync``), but instead of landing locally it pushes the
    source's tip to ``origin`` as branch ``<goal>`` (the actor suffix is local plumbing;
    the goal is the publication) and opens a PR against ``into`` (default: the repo's
    default branch) via ``gh``. The base must already be on origin — the PR targets
    origin's branch, so a local-only base refuses before anything is pushed. Nothing is
    deleted or stopped — the goal keeps working until the PR lands, after which
    ``goal merge`` (or ``goal finish``, once a fetch shows the work contained) cleans up
    as usual.

    Title and body: a single-commit branch reuses its subject and body verbatim — the
    same content GitHub itself would prefill from the commit, computed here so ``dry``
    can preview it. A multi-commit branch's description is *written*, by a model working
    from the project's own PR prompt (:func:`_compose` — ``prompts/pr.md`` when present,
    else the packaged default asking for a succinct why with any referenced tickets and
    issues linked, never a diff or commit-list restatement) — but only when no PR is
    already open (its description stands; the push alone updates it) — and the answer is
    cached and reused while the prompt is unchanged, so a ``--dry`` preview is exactly
    what the later run ships (see :func:`_compose`). A model failure refuses outright
    rather than shipping a placeholder — the PR is the deliverable.

    Idempotent: a re-run pushes the branch again (a no-op when unchanged; git refuses a
    non-fast-forward, e.g. after a rebase — resolve that by hand) and, finding the PR
    already open, reports it instead of opening a duplicate. The pushed remote-tracking
    ref rides a ``goal pr: refs`` log line; the PR itself lands ``goal pr: opened`` (or
    ``goal pr: existing``). Under ``dry`` everything — source, commits, title, body —
    resolves for real, but nothing is pushed and no PR is opened.
    """
    git = Git(repo)
    if 'origin' not in git('remote').split():
        raise UserError(
            f'no origin to push {goal} to — publish the project first: ch project push <url>'
        )
    if fetch:
        fetch_origin_or_offline(git)
    actors = sorted(goal_branch_actors(git, goal))
    if not actors:
        raise UserError(f'nothing to propose — no actor branches for goal {goal!r}')
    base = into if into is not None else default_branch(git)
    if base.startswith(f'{goal}/'):
        raise UserError(f"{base} is one of {goal}'s own branches — name a base like main")
    source = source_branch(git, goal, actors, command='goal pr', or_force=False)
    # the base must already be on origin — gh resolves it server-side, so anything else
    # (a local-only branch, or a remote-tracking `origin/main` spelling DWIM would let
    # through) could only fail in `gh pr create`, after the push; commits are compared
    # against origin's view of it, since a local base's own unpushed commits aren't ours
    if not git.ref_exists(f'refs/remotes/origin/{base}'):
        if git.ref_exists(f'refs/heads/{base}'):
            raise UserError(
                f'{base} exists locally but not on origin, where the PR needs it — '
                f'git push origin {base}, then re-run'
            )
        raise UserError(f'no branch {base} to propose against')
    compared = f'origin/{base}'
    commits = _commits(git, compared, source)
    if not commits:
        raise UserError(f'{source} has no commits beyond {compared} — nothing to propose')
    existing = _existing(repo, goal)
    if len(commits) == 1:
        title, body = commits[0]  # what github itself would prefill from the lone commit
    elif existing is None:
        title, body = _compose(
            _description_cache(git, goal), prompts_dir, project, goal, base, source, commits
        )
    else:
        title, body = '', ''  # the open PR already carries its description; nothing to write
    with git.ref_log('goal pr: refs', f'origin/{goal}', goal=goal, source=source):
        dry(git, 'push', 'origin', f'{source}:refs/heads/{goal}')
    if existing is not None:
        logger.bind(url=existing, goal=goal).info('goal pr: existing')
        url, created = existing, False
    elif dry.on:  # opening returns the URL, so it can't ride the Dry guard's void call
        url, created = None, False
    else:
        url = _create(repo, goal, base, title, body, draft)
        logger.bind(url=url, title=title, goal=goal).info('goal pr: opened')
        created = True
    return PrResult(source, goal, base, git.rev_parse(source), title, body, url, created)


def _commits(git: Git, compared: str, source: str) -> list[tuple[str, str]]:
    """The branch's own commits as ``(subject, body)`` pairs, oldest first."""
    raw = git('log', '--reverse', '--format=%s%x1f%b%x1e', f'{compared}..{source}')
    pairs = []
    for entry in raw.split('\x1e'):
        if entry.strip():
            subject, _, body = entry.partition('\x1f')
            pairs.append((subject.strip(), body.strip()))
    return pairs


def _description_cache(git: Git, goal: str) -> Path:
    """Where :func:`_compose` caches the goal's written description, in the shared git dir
    beside ``goal sync``'s append markers; ``goal finish`` sweeps both."""
    common = Path(git('rev-parse', '--path-format=absolute', '--git-common-dir').strip())
    return common / 'chimera' / 'pr' / goal


def _compose(
    cache: Path,
    prompts_dir: Path,
    project: str,
    goal: str,
    base: str,
    source: str,
    commits: list[tuple[str, str]],
) -> tuple[str, str]:
    """A multi-commit branch's ``(title, body)``, written by a model to the project's spec.

    The prompt is the project's ``prompts/pr.md`` when present, else the packaged default —
    the customisation point: each project's template encodes its own PR dance (required
    sections, ticket-linking conventions, tone), while the default asks for a succinct
    *why* with any referenced issues or tickets linked, and forbids restating the diff or
    the commit list. Rendered with :class:`string.Template` (``$PROJECT``, ``$GOAL``,
    ``$BASE``, ``$SOURCE``, ``$COMMITS`` — the branch's full commit messages, oldest
    first). The model's first output line is the title, the rest the body. Any failure —
    no model, non-zero exit, empty answer — refuses with the ``gh pr create`` line to run
    by hand: a placeholder description is worse than a human moment.

    The answer is cached at ``cache``, keyed by a hash of the exact prompt, and reused
    while that prompt is unchanged — so the title and body a ``--dry`` previewed are
    byte-for-byte what the later real run ships, never a fresh model run's different
    words. Any change to the commits, template or targets misses the key and recomposes;
    each way lands a ``goal pr: description`` line binding the path, key and whether it
    was reused.
    """
    override = prompts_dir / 'pr.md'
    text = override.read_text() if override.exists() else _default_template()
    log_text = '\n\n'.join(f'{subject}\n\n{body}' if body else subject for subject, body in commits)
    prompt = Template(text).safe_substitute(
        PROJECT=project, GOAL=goal, BASE=base, SOURCE=source, COMMITS=log_text
    )
    key = sha256(prompt.encode()).hexdigest()
    if cache.is_file():
        stored, _, cached = cache.read_text().partition('\n')
        if stored == key:
            logger.bind(path=str(cache), sha256=key, reused=True).info('goal pr: description')
            return _title_and_body(cached)
    by_hand = f'write it yourself: gh pr create --head {goal} --base {base}'
    try:
        result = subprocess.run(
            ['claude', '-p', prompt, '--model', _COMPOSE_MODEL],
            input='',  # claude in print mode reads piped stdin to EOF — never hand it ours
            capture_output=True,
            text=True,
            check=True,
        )
    except OSError as error:
        raise UserError(f'could not write the PR description ({error}) — {by_hand}') from None
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or '').strip() or str(error)
        raise UserError(f'could not write the PR description ({detail}) — {by_hand}') from None
    output = result.stdout.strip()
    title, body = _title_and_body(output)
    if not title:
        raise UserError(f'the PR description model answered nothing — {by_hand}')
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(f'{key}\n{output}')
    logger.bind(path=str(cache), sha256=key, reused=False).info('goal pr: description')
    return title, body


def _title_and_body(output: str) -> tuple[str, str]:
    """Split a composed description: first line is the title, the rest the body."""
    title, _, body = output.partition('\n')
    return title.strip(), body.strip()


def _default_template() -> str:
    """The packaged fallback PR prompt, shipped as ``chimera`` package data."""
    return (files('chimera.prompts') / 'pr.md').read_text()


def _existing(repo: Path, head: str) -> str | None:
    """The URL of an already-open PR for branch ``head``, or None when there isn't one."""
    result = subprocess.run(
        ['gh', 'pr', 'list', '--head', head, '--state', 'open', '--json', 'url'],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise UserError(f'gh pr list failed: {result.stderr.strip()}')
    rows = json.loads(result.stdout)
    return str(rows[0]['url']) if rows else None


def _create(repo: Path, head: str, base: str, title: str, body: str, draft: bool) -> str:
    """Open the PR via ``gh`` and return its URL (the last line gh prints)."""
    args = ['gh', 'pr', 'create', '--head', head, '--base', base, '--title', title, '--body', body]
    if draft:
        args.append('--draft')
    result = subprocess.run(args, cwd=repo, capture_output=True, text=True)
    if result.returncode != 0:
        raise UserError(f'gh pr create failed: {result.stderr.strip()}')
    return result.stdout.strip().splitlines()[-1]
