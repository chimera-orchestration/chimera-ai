"""Chimera's Git: giterator's, plus command tracing, ref-mutation logging and network timeouts.

Every chimera module uses this subclass (never ``giterator.Git`` directly), so
:meth:`Git.__call__` is the single choke point every git subprocess runs through:

- **Tracing** — each command lands a DEBUG line *before* it runs (a hung fetch is visible while
  it hangs): the message is the exact command, the working directory rides the ``git_cwd`` key.
  The console echo of these lines is a sink concern (see :func:`chimera.logging.configure`);
  nothing here prints. Suppressed during shell completion (a completer must never print).
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

from giterator import Git as GiteratorGit
from giterator import GitError
from loguru import logger

SSH_COMMAND = 'ssh -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=4'
"""``GIT_SSH_COMMAND`` when the user hasn't set one: fail a dead connect/handshake in 10s
(``ConnectTimeout`` covers TCP connect *and* the banner exchange/KEX — the observed VPN hang)
and a mid-transfer dead peer in ~60s."""

HTTP_TIMEOUTS = {'GIT_HTTP_LOW_SPEED_LIMIT': '1024', 'GIT_HTTP_LOW_SPEED_TIME': '30'}
"""Abort an HTTPS transfer crawling under 1KB/s for 30s, unless the user tuned their own."""

_COMPLETION_VARS = ('_CH_COMPLETE', '_CHIMERA_COMPLETE')


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
        if not any(var in os.environ for var in _COMPLETION_VARS):
            logger.bind(git_cwd=str(cwd or self.path)).debug(shlex.join(('git', *command)))
        return super().__call__(*command, env=_env(os.environ, env), cwd=cwd or self.path)

    git = __call__

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
