"""
``ch logtail`` — the human view of the workspace's action log.

The log is one-line JSON (see ``chimera.logging``) whose most important records — the
start/end frames — carry an *empty* ``message``: their content rides bound fields
(``caller``, ``command``, ``phase``, ``duration_ms``, ``error``). So a raw ``tail -f`` is unreadable
and a generic JSON viewer shows blank lines exactly where it matters. Rather than
hand-rolling a renderer, ``tail`` is piped through `fblog <https://github.com/brocode/fblog>`_
with a main-line format tuned to those fields; ``ch doctor`` checks fblog is installed
(``--fix`` brew-installs it).
"""

import shutil
import subprocess
from pathlib import Path

from chimera.config import UserError
from chimera.logging import log_path

FBLOG = 'fblog'

# Width of the caller id column: room for a typical `<project>@<goal>@agent` address; a
# longer one keeps its head (project and goal — the distinguishing bits) and loses the tail.
ID_WIDTH = 32

# fblog's handlebars main-line format, tuned to chimera's fields: the fixed-width caller id
# (who ran it — guarded, since lines predating the field would otherwise fail to render),
# then command + the goal a goal-scoped action carries + phase for the frame lines whose
# message is empty; durations and errors ride the end frames. Tracebacks are deliberately
# left out of the tail view — `--dump` is the post-mortem surface.
FORMAT = (
    '{{bold(fixed_size 19 fblog_timestamp)}} '
    '{{level_style (fixed_size 5 fblog_level)}} '
    '{{#if caller}}{{fixed_size %(width)d caller}}{{else}}%(pad)s{{/if}} '
    '{{#if command}}{{bold(cyan command)}} {{/if}}'
    '{{#if goal}}{{yellow goal}} {{/if}}'
    '{{#if phase}}[{{phase}}] {{/if}}'
    '{{fblog_message}}'
    '{{#if duration_ms}} ({{duration_ms}}ms){{/if}}'
    '{{#if error}} {{red error}}{{/if}}'
) % {'width': ID_WIDTH, 'pad': ' ' * ID_WIDTH}


class FblogMissingError(UserError):
    def __init__(self) -> None:
        super().__init__('fblog is not installed — `brew install fblog` or `ch doctor --fix`')


class NoLogError(UserError):
    def __init__(self, log: Path) -> None:
        super().__init__(f'nothing logged yet at {log}')


def tail_args(log: Path, lines: int, follow: bool) -> list[str]:
    """
    The ``tail`` command producing the log lines: the last ``lines`` of ``log``, following
    (``-F``, so rotation/recreation is survived) unless told not to.
    """
    return ['tail', '-n', str(lines), *(['-F'] if follow else []), str(log)]


def fblog_args(dump: bool) -> list[str]:
    """
    The ``fblog`` command rendering the lines: the tuned one-line view (:data:`FORMAT`), or
    with ``dump`` every field of every record (params, git before/after maps, tracebacks).
    """
    return [FBLOG, '-d'] if dump else [FBLOG, '--main-line-format', FORMAT]


def pipeline(producer: list[str], consumer: list[str]) -> int:
    """
    Run ``producer | consumer`` wired to this process's stdout, returning the consumer's
    exit code. Ctrl-C — the normal way a follow ends — lands on the whole foreground process
    group, so both children die with us: swallow it and report a clean exit.
    """
    tail = subprocess.Popen(producer, stdout=subprocess.PIPE)
    assert tail.stdout is not None
    try:
        return subprocess.run(consumer, stdin=tail.stdout).returncode
    except KeyboardInterrupt:
        return 0
    finally:
        tail.stdout.close()
        if tail.poll() is None:
            tail.terminate()
        tail.wait()


def logtail(workspace: Path, lines: int = 20, follow: bool = True, dump: bool = False) -> int:
    """
    Tail the workspace's action log through fblog, returning the pipeline's exit code.
    """
    if shutil.which(FBLOG) is None:
        raise FblogMissingError()
    log = log_path(workspace)
    if not log.exists():
        raise NoLogError(log)
    return pipeline(tail_args(log, lines, follow), fblog_args(dump))
