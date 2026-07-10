import json
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

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

# The lightweight model `_title` asks to compress a multi-commit branch's messages into a
# PR title. It only ever sees text a human or agent already wrote and committed — it
# compresses, never invents — and any failure falls back to the goal name, so the title is
# always honest even with no model reachable.
_TITLE_MODEL = 'haiku'
_TITLE_PROMPT = (
    'Write a single-line pull-request title (max 60 chars, imperative mood, no trailing '
    'period, no type prefix) saying WHY this branch exists, not which files changed. The '
    "branch's git commit messages are on stdin; compress them faithfully — never mention "
    'anything they do not. Output only the title.'
)


@dataclass(frozen=True)
class PrResult:
    """What proposing the goal did: what was pushed where, and the PR it landed in."""

    source: str  # the actor branch whose tip was pushed
    remote_branch: str  # the branch name on origin (the goal name)
    base: str  # the branch the PR targets
    sha: str  # the pushed tip (short)
    title: str
    body: str
    url: str | None  # the PR's URL; None only under dry when none is open yet
    created: bool  # False when a PR for the branch was already open (push still updates it)


def pr(
    repo: Path,
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
    default branch) via ``gh``. Nothing is deleted or stopped — the goal keeps working
    until the PR lands, after which ``goal merge`` (or ``goal finish``, once a fetch
    shows the work contained) cleans up as usual.

    Title and body are **derived from the commit messages, never from the diff**: a
    single-commit branch reuses its subject and body verbatim; a multi-commit branch gets
    its messages listed as the body, with a one-line title compressed from them by a
    lightweight model (:data:`_TITLE_PROMPT` — goal name as the fallback when the model
    can't be reached or answers nothing).

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
    compared = f'origin/{base}' if git.ref_exists(f'origin/{base}') else base
    if not git.ref_exists(compared):
        raise UserError(f'no branch {base} to propose against')
    commits = _commits(git, compared, source)
    if not commits:
        raise UserError(f'{source} has no commits beyond {compared} — nothing to propose')
    title, body = _title(goal, commits), _body(commits)
    existing = _existing(repo, goal)
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


def _title(goal: str, commits: list[tuple[str, str]]) -> str:
    """The PR title: a lone commit's subject verbatim, else a model-compressed one-liner.

    The commit subjects already carry the why (that's the house commit style), so a
    single-commit branch needs no generation at all. A multi-commit branch feeds its
    messages — only its messages, never the diff — to a lightweight model to compress;
    any failure or empty answer falls back to the goal name rather than inventing.
    """
    if len(commits) == 1:
        return commits[0][0]
    log_text = '\n\n'.join(f'{subject}\n{body}' if body else subject for subject, body in commits)
    try:
        result = subprocess.run(
            ['claude', '-p', _TITLE_PROMPT, '--model', _TITLE_MODEL],
            input=log_text,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        logger.bind(goal=goal).warning('goal pr: title model unreachable, using the goal name')
        return goal
    lines = result.stdout.strip().splitlines()
    return lines[0].strip() if lines else goal


def _body(commits: list[tuple[str, str]]) -> str:
    """The PR body: the commit messages themselves — provenance, not generated prose.

    A lone commit's body rides verbatim; several are listed oldest-first, each subject a
    bullet with its body indented under it. The diff speaks for itself on the PR — the
    body only has to say why, and the commits already do.
    """
    if len(commits) == 1:
        return commits[0][1]
    blocks = []
    for subject, body in commits:
        block = f'- {subject}'
        if body:
            block += '\n\n' + textwrap.indent(body, '  ')
        blocks.append(block)
    return '\n\n'.join(blocks)


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
