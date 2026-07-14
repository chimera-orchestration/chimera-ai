import json
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from string import Template

from giterator import GitError
from loguru import logger

from chimera.commands.goal.merge import source_branch
from chimera.config import UserError
from chimera.dry import Dry
from chimera.git import Git, remote_repo, remote_slug
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
    remote: str  # the remote the branch was pushed to (origin unless --to)
    remote_branch: str  # the branch name on the remote (the goal name)
    head: str  # gh's head spec: the branch, owner-qualified when the push was cross-repo
    base: str  # the branch the PR targets, always on origin
    sha: str  # the pushed tip (short)
    title: str  # with body, '' when composing was skipped: an open PR already has its own
    body: str
    url: str | None  # the PR's URL; None only under dry when none is open yet
    created: bool  # False when a PR for the branch was already open (push still updates it)
    cleared: tuple[str, ...] = ()  # stale <goal>/<actor> refs deleted from the push remote


def pr(
    repo: Path,
    project: str,
    prompts_dir: Path,
    goal: str,
    into: str | None = None,
    draft: bool = False,
    to: str | None = None,
    fetch: bool = True,
    dry: Dry = Dry(),
) -> PrResult:
    """Publish a finished goal as a pull request, leaving every local branch standing.

    The remote-review sibling of ``goal merge``: the same source selection (the actor
    branch containing every other actor's work — :func:`source_branch`; diverged actors
    refuse, pointing at ``goal sync``), but instead of landing locally it pushes the
    source's tip to ``to`` (default: ``origin``; must name an existing remote) as branch
    ``<goal>`` (the actor suffix is local plumbing; the goal is the publication) and
    opens a PR against ``into`` (default: the repo's default branch) via ``gh``. The base
    must already be on origin whatever ``to`` names — the PR always targets origin's
    branch — so a local-only base refuses before anything is pushed, and a non-origin
    ``to`` (the fork workflow: origin readable but not writable) opens a *cross-repo* PR,
    its head qualified by the fork's owner as read from the remote's URL
    (:func:`_head`). Nothing is deleted or stopped — the goal keeps working until the PR
    lands, after which ``goal merge`` (or ``goal finish``, once a fetch shows the work
    contained) cleans up as usual.

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
    remotes = git('remote').split()
    if 'origin' not in remotes:  # the PR targets origin's base, wherever the push goes
        raise UserError(
            f'no origin to propose {goal} against — publish the project first: '
            f'ch project push <url>'
        )
    remote = to if to is not None else 'origin'
    if remote not in remotes:
        raise UserError(f'no remote {remote!r} to push {goal} to (remotes: {", ".join(remotes)})')
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
    head, owner = _head(git, remote, goal)
    cleared = _clear_blocked_namespace(git, remote, goal, base, dry)
    # the PR lives on origin's repo whatever the push remote — with a second github remote
    # around (the fork), gh can't infer that base repo itself, so pin it (host-qualified:
    # a bare owner/repo would aim a GitHub-Enterprise origin at github.com) when derivable
    origin_url = git('remote', 'get-url', 'origin').strip()
    origin_repo = remote_repo(origin_url)
    if owner is None and (origin_slug := remote_slug(origin_url)):
        owner = origin_slug.split('/', 1)[0]  # a same-repo head is origin's own owner
    existing = _existing(repo, goal, owner, origin_repo)
    if len(commits) == 1:
        title, body = commits[0]  # what github itself would prefill from the lone commit
    elif existing is None:
        title, body = _compose(
            _description_cache(git, goal), prompts_dir, project, goal, head, base, source, commits
        )
    else:
        title, body = '', ''  # the open PR already carries its description; nothing to write
    _push(git, remotes, remote, source, goal, dry)
    if existing is not None:
        logger.bind(url=existing, goal=goal).info('goal pr: existing')
        url, created = existing, False
    elif dry.on:  # opening returns the URL, so it can't ride the Dry guard's void call
        url, created = None, False
    else:
        url = _create(repo, head, base, title, body, draft, origin_repo)
        logger.bind(url=url, title=title, goal=goal).info('goal pr: opened')
        created = True
    return PrResult(
        source, remote, goal, head, base, git.rev_parse(source), title, body, url, created, cleared
    )


def _clear_blocked_namespace(
    git: Git, remote: str, goal: str, base: str, dry: Dry
) -> tuple[str, ...]:
    """Clear stale ``<goal>/<actor>`` branches on ``remote`` that would block ``<goal>``.

    Git can't hold ``refs/heads/<goal>`` beside ``refs/heads/<goal>/*`` (the same
    file/directory conflict ``goal adopt`` restructures around locally), so an actor
    branch left on the push remote — e.g. pushed there by hand for an earlier PR —
    would fail the push outright. One contained in the PR's base is dead weight from
    that landed round: it's deleted, its sha on the log line for recovery. One carrying
    work the base doesn't have refuses, naming the exact ref — deleting it would be the
    one loss the log can't undo. Listed via the remote's *push* URL, the side the
    collision lives on.
    """
    push_url = git('remote', 'get-url', '--push', remote).strip()
    listed = git('ls-remote', push_url, f'refs/heads/{goal}/*').strip()
    if not listed:
        return ()
    stale: dict[str, str] = {}
    for line in listed.splitlines():
        sha, _, ref = line.partition('\t')
        stale[ref.strip().removeprefix('refs/heads/')] = sha.strip()
    blockers = sorted(
        ref for ref, sha in stale.items() if not _contained(git, sha, f'origin/{base}')
    )
    if blockers:
        raise UserError(
            f'{", ".join(blockers)} on {remote} blocks creating branch {goal} and has '
            f'work origin/{base} does not — delete or rename it on {remote}, then re-run'
        )
    before = {f'{remote}/{ref}': sha for ref, sha in sorted(stale.items())}
    for ref in sorted(stale):
        dry(git, 'push', remote, f':refs/heads/{ref}')
    if not dry.on:  # hand-rolled ref_log shape: these refs live on the remote, not here
        logger.bind(git={'before': before, 'after': {}}, remote=remote, goal=goal).info(
            'goal pr: refs'
        )
    return tuple(sorted(before))


def _contained(git: Git, sha: str, base: str) -> bool:
    """Whether ``sha`` is an ancestor of ``base`` — False too when the object isn't even
    held locally (never fetched), which is just as unsafe to delete."""
    try:
        git('merge-base', '--is-ancestor', sha, base)
    except GitError:
        return False
    return True


def _head(git: Git, remote: str, goal: str) -> tuple[str, str | None]:
    """``gh``'s head spec for the pushed branch, with the fork's owner when cross-repo.

    Pushed to origin the head is normally the branch itself: the PR is same-repo, and
    None rides along as the owner. Pushed anywhere else the PR must be *cross-repo*, so
    gh needs ``<owner>:<branch>`` — the owner read from the remote's URL, never
    hardcoded. The push URL's identity wins when it parses (a triangular origin whose
    pushurl names a different repo really is a fork push); a pushurl that carries no
    identity (a local path, an alternate transport) defers to the fetch URL. A remote
    neither of whose URLs names an owner can't front a github PR, so a non-origin one
    refuses before anything is pushed.
    """
    fetch_slug = remote_slug(git('remote', 'get-url', remote).strip())
    push_url = git('remote', 'get-url', '--push', remote).strip()
    slug = remote_slug(push_url) or fetch_slug  # where the branch actually lands
    if remote == 'origin' and slug == fetch_slug:
        return goal, None
    if not slug:
        raise UserError(
            f"can't tell whose fork {remote} is — its URL ({push_url}) names no owner "
            f'to qualify the cross-repo PR head with'
        )
    owner = slug.split('/', 1)[0]
    return f'{owner}:{goal}', owner


def _push(git: Git, remotes: list[str], remote: str, source: str, goal: str, dry: Dry) -> None:
    """Push the source tip to ``remote`` as the goal branch, translating a denied push.

    A push rejected for lack of write access — the fork workflow's read-only origin — is
    the one push failure with a chimera answer (name a writable remote with ``--to``),
    so it lands as a UserError carrying that hint; any other failure raises as itself.
    Detection reads only git's own output (never the command line the error message
    leads with, whose refspec would make a goal named e.g. ``denied`` match); a branch
    name inside the remote's rejection lines is an accepted residual.

    The push always runs from the project repo — never an agent worktree, whose HEAD a
    pre-push hook may treat differently.
    """
    with git.ref_log('goal pr: refs', f'{remote}/{goal}', goal=goal, source=source):
        try:
            dry(git, 'push', remote, f'{source}:refs/heads/{goal}')
        except GitError as error:
            detail = str(error).strip()
            output = detail.partition('\n\n')[2]  # giterator leads with the command line
            if 'denied' not in output.lower() and '403' not in output:
                raise
            others = ', '.join(sorted(set(remotes) - {remote}))
            extra = f' (this repo also has: {others})' if others else ''
            raise UserError(
                f'{remote} denied the push:\n{detail}\n'
                f'no write access? push via a writable remote: '
                f'ch goal pr {goal} --to <remote>{extra}'
            ) from None


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
    head: str,
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
    by_hand = f'write it yourself: gh pr create --head {head} --base {base}'
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


def _repo_flag(origin_repo: str) -> list[str]:
    """``--repo`` pinning gh to origin's repo, where the PR lives whatever remote was
    pushed — with a second github remote around (the fork), gh refuses to infer a base
    repo itself. Host-qualified (``host/owner/repo``): gh reads a bare pair as
    github.com's. Empty when the origin has no hosted identity to pin (a local path)."""
    return ['--repo', origin_repo] if origin_repo else []


def _existing(repo: Path, branch: str, owner: str | None, origin_repo: str) -> str | None:
    """The URL of an already-open PR for ``branch``, or None when there isn't one.

    ``gh pr list --head`` matches the bare branch name whichever repo the head lives in,
    so when the expected head owner is known — the fork's for a cross-repo push,
    origin's own otherwise — the rows are filtered on the head repository's owner:
    a stranger's same-named branch must never shadow (or stand in for) ours. A null
    owner (gh's spelling for a deleted head fork) can't be ours. Ownerless only when
    the origin itself has no hosted identity to compare.
    """
    fields = 'url' if owner is None else 'url,headRepositoryOwner'
    result = subprocess.run(
        ['gh', 'pr', 'list', '--head', branch, '--state', 'open', '--json', fields]
        + _repo_flag(origin_repo),
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise UserError(f'gh pr list failed: {result.stderr.strip()}')
    rows = json.loads(result.stdout)
    if owner is not None:
        rows = [
            row
            for row in rows
            if str((row['headRepositoryOwner'] or {}).get('login', '')).lower() == owner
        ]
    return str(rows[0]['url']) if rows else None


def _create(
    repo: Path, head: str, base: str, title: str, body: str, draft: bool, origin_repo: str
) -> str:
    """Open the PR via ``gh`` (``head`` owner-qualified when cross-repo) and return its URL
    (the last line gh prints)."""
    args = ['gh', 'pr', 'create', '--head', head, '--base', base, '--title', title, '--body', body]
    args += _repo_flag(origin_repo)
    if draft:
        args.append('--draft')
    result = subprocess.run(args, cwd=repo, capture_output=True, text=True)
    if result.returncode != 0:
        raise UserError(f'gh pr create failed: {result.stderr.strip()}')
    return result.stdout.strip().splitlines()[-1]
