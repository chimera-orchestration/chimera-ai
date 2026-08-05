"""Chimera's Git: giterator's, plus command tracing, ref-mutation logging and network timeouts.

Every chimera module uses this subclass (never ``giterator.Git`` directly), so its two entry
points are what every git subprocess runs through: :meth:`Git.__call__` for output that is
text (and may carry ``env`` overrides or a :meth:`Git.ref_log` block), :meth:`Git.raw` for
output that isn't — diff text and ``-z`` file names, which git never transcodes. ``raw``
carries neither of those extras deliberately: it is the byte path, not a second general one.
Both behave alike otherwise:

- **Tracing** — each command lands a DEBUG line *before* it runs (a hung fetch is on record
  while it hangs): the message is the exact command, the working directory rides the ``git_cwd``
  key. Where the lines go is entirely a sink concern (see :func:`chimera.logging.configure` —
  the workspace's log file); nothing here prints, and nothing here decides when to stay
  quiet, so muting a context (``__main__.main`` drops the sinks while completing) never
  needs a second guard at this end.
- **Ref-mutation logging** — :meth:`Git.ref_log` wraps a mutating block in the before/after
  snapshot ``agent-docs/logging.md`` mandates, so call sites can't drift from the rule.
- **Network timeouts** — a stalled SSH/HTTPS transport fails in seconds instead of hanging
  ``ch`` indefinitely (:data:`SSH_COMMAND`, :data:`HTTP_TIMEOUTS`), injected via environment
  variables only when the user hasn't set them. Known limit: ``GIT_SSH_COMMAND`` outranks a
  user's ``core.sshCommand`` gitconfig, so that (rare) config is shadowed by our default.
"""

import os
import shlex
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from subprocess import PIPE, run
from urllib.parse import urlsplit

from giterator import Git as GiteratorGit
from giterator import GitError
from loguru import logger

SSH_COMMAND = 'ssh -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=4'
"""``GIT_SSH_COMMAND`` when the user hasn't set one: fail a dead connect/handshake in 10s
(``ConnectTimeout`` covers TCP connect *and* the banner exchange/KEX — the observed VPN hang)
and a mid-transfer dead peer in ~60s."""

HTTP_TIMEOUTS = {'GIT_HTTP_LOW_SPEED_LIMIT': '1024', 'GIT_HTTP_LOW_SPEED_TIME': '30'}
"""Abort an HTTPS transfer crawling under 1KB/s for 30s, unless the user tuned their own."""


def repo_slug(path: str) -> str:
    """``owner/repo`` (lowercased) from a URL path like ``/owner/repo/pull/<n>``; '' if too short."""
    parts = path.strip('/').split('/')
    return '/'.join(parts[:2]).lower() if len(parts) >= 2 else ''


def remote_slug(url: str) -> str:
    """``owner/repo`` from a github-style remote URL (https/ssh/scp); '' for a bare local path.

    A ``://`` URL must carry a host (``file:///…`` has none — its path is a filesystem
    path, never an owner/repo pair).
    """
    text = url.removesuffix('.git')
    if '://' in text:
        split = urlsplit(text)
        return repo_slug(split.path) if split.netloc else ''
    if ':' in text and '/' not in text.split(':', 1)[0]:  # scp-like git@host:owner/repo
        return repo_slug(text.split(':', 1)[1])
    return ''  # a local-path remote has no hosted identity to compare


def remote_repo(url: str) -> str:
    """``host/owner/repo`` from a github-style remote URL; '' when it has no hosted identity.

    The form ``gh --repo`` wants: without the host, gh assumes github.com, silently
    aiming a GitHub-Enterprise remote's owner/repo at the wrong service.
    """
    slug = remote_slug(url)
    if not slug:
        return ''
    text = url.removesuffix('.git')
    if '://' in text:
        host = urlsplit(text).netloc.rpartition('@')[2]
    else:
        host = text.split(':', 1)[0].rpartition('@')[2]
    return f'{host}/{slug}' if host else ''


def sibling_url(url: str, slug: str) -> str:
    """``url`` with its ``owner/repo`` swapped for ``slug`` — same scheme, host and credentials.

    How a fork's URL is derived from origin's: the fork lives on the same host and answers
    to the same credentials, only the slug differs. '' when ``url`` carries no hosted
    identity to swap (a local path), mirroring :func:`remote_slug`.
    """
    if not remote_slug(url):
        return ''
    text = url.removesuffix('.git')
    suffix = '.git' if text != url else ''
    if '://' in text:
        split = urlsplit(text)
        return f'{split.scheme}://{split.netloc}/{slug}{suffix}'
    return f'{text.split(":", 1)[0]}:{slug}{suffix}'  # scp-like git@host:owner/repo


def _env(base: Mapping[str, str], caller: dict[str, str] | None) -> dict[str, str]:
    """``base`` (the process environment) plus the timeout defaults it doesn't already set,
    with any ``caller`` overrides merged last."""
    merged = dict(base)
    if not {'GIT_SSH', 'GIT_SSH_COMMAND'} & base.keys():
        merged['GIT_SSH_COMMAND'] = SSH_COMMAND
    for key, value in HTTP_TIMEOUTS.items():
        merged.setdefault(key, value)
    if caller:
        merged.update(caller)
    return merged


class RefLog:
    """The handle :meth:`Git.ref_log` yields: ``bind`` extra keys onto the eventual log line
    for values only known mid-block (e.g. a worktree path the block creates)."""

    def __init__(self) -> None:
        self.extra: dict[str, object] = {}

    def bind(self, **extra: object) -> None:
        self.extra.update(extra)


class Git(GiteratorGit):
    """See the module docstring — trace, ref-log and timeout behaviour all live here."""

    def __call__(
        self, *command: str, env: dict[str, str] | None = None, cwd: Path | None = None
    ) -> str:
        self._trace(command, cwd or self.path)
        return super().__call__(*command, env=_env(os.environ, env), cwd=cwd or self.path)

    git = __call__

    def _trace(self, command: tuple[str, ...], cwd: Path) -> None:
        logger.bind(git_cwd=str(cwd)).debug(shlex.join(('git', *command)))

    def raw(self, *command: str) -> bytes:
        """Run a git command, returning its stdout as raw bytes.

        For output that splices blob content in (``log -p``, ``diff``) or ``-z`` file names:
        git never transcodes, so none of that is guaranteed UTF-8 — decoding it crashes on
        the first latin-1 file in someone's history. stderr stays out of the return value
        (callers parse these bytes; a stray ``warning:`` would corrupt them) and joins the
        :class:`GitError` on failure.
        """
        self._trace(command, self.path)
        result = run(
            ('git', *command), cwd=self.path, stdout=PIPE, stderr=PIPE, env=_env(os.environ, None)
        )
        if result.returncode:
            raise GitError(
                f'{" ".join(("git", *command))!r} gave return code {result.returncode}:\n\n'
                f'{(result.stderr + result.stdout).decode(errors="replace")}\n\n'
            )
        return result.stdout

    def ref_exists(self, ref: str) -> bool:
        try:
            self('rev-parse', '--verify', '--quiet', ref)
            return True
        except GitError:
            return False

    def ref_shas(self, *refs: str) -> dict[str, str]:
        """Each of ``refs`` that currently exists, mapped to the full sha it points at.

        The before/after snapshot for logging a ref mutation (see ``agent-docs/logging.md``):
        capture it either side of the change so the log alone can restore a ref.
        """
        return {ref: self.rev_parse(ref, short=False) for ref in refs if self.ref_exists(ref)}

    @contextmanager
    def ref_log(
        self, message: str, *refs: str, always: bool = False, **bind: object
    ) -> Iterator[RefLog]:
        """Log any mutation of ``refs`` made by the wrapped block, per ``agent-docs/logging.md``.

        Snapshots the refs' shas either side of the block and lands one ``message`` line with
        the ``{before, after}`` maps — skipped when nothing changed, unless ``always`` (sites
        whose log is the recovery record even for a no-op re-run, e.g. ``goal adopt``).
        ``bind`` keys ride the line; the yielded :class:`RefLog` binds ones only known
        mid-block. The ``after`` snapshot runs in a ``finally``, so a block that dies half-way
        still records every mutation it completed — the log must be enough to undo them.
        """
        before = self.ref_shas(*refs)
        handle = RefLog()
        try:
            yield handle
        finally:
            after = self.ref_shas(*refs)
            if always or after != before:
                logger.bind(
                    git={'before': before, 'after': after}, **{**bind, **handle.extra}
                ).info(message)
