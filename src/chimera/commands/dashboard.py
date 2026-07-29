"""``ch dashboard``: a colorized, columnar human view over the same :class:`~chimera.
commands.ls.Board` ``ch ls`` builds — for running under ``watch``, not for an agent to
parse (``ch ls`` stays plain-text for that; see ``agent_env.RESTRICTED_COMMANDS``).
"""

from dataclasses import dataclass

import typer

from chimera.agents import AgentSession
from chimera.archive import ArchiveSession
from chimera.commands.ls import Board, Mail, Row

DETAIL_MAX = 80

_HEADERS = ('NAME', 'STATUS', 'DETAIL', 'NEW', 'CUR', 'DONE')

_LIVE_COLOR = {'busy': 'green', 'running': 'green', 'stale': 'red'}
_ARCHIVED_COLOR = 'bright_black'
_NEVER_RUN = 'never run'


@dataclass(frozen=True)
class _Line:
    """One renderable row: an indent level, a name, a status (with its color), a detail,
    and mail counts — ``None`` mail marks a section header (a project or goal name)."""

    indent: int
    name: str
    status: str
    status_color: str | None
    detail: str
    mail: Mail | None


def _truncate(text: str) -> str:
    return text if len(text) <= DETAIL_MAX else text[: DETAIL_MAX - 1] + '…'


def _live_line(indent: int, name: str, a: AgentSession) -> _Line:
    status = 'stale' if a.stale is not None else a.status
    detail = a.stale if a.stale is not None else a.detail
    return _Line(indent, name, status, _LIVE_COLOR.get(status), _truncate(detail), None)


def _archived_line(indent: int, name: str, s: ArchiveSession) -> _Line:
    return _Line(indent, name, s.status, _ARCHIVED_COLOR, '', None)


def _row_line(indent: int, row: Row) -> _Line:
    """A structural slot's line, named by its address in the NAME column — the archive's
    own name for the slot, not necessarily the live registry's title."""
    if row.live is not None:
        line = _live_line(indent, row.address, row.live)
    elif row.last is not None:
        line = _archived_line(indent, row.address, row.last)
    else:
        line = _Line(indent, row.address, _NEVER_RUN, _ARCHIVED_COLOR, '', None)
    return _Line(line.indent, line.name, line.status, line.status_color, line.detail, row.mail)


def _flatten(b: Board) -> list[_Line]:
    """Every renderable line, in the same order ``ch ls`` walks the tree."""
    lines = [_row_line(0, b.captain)]
    for p in b.projects:
        lines.append(_Line(0, p.name, '', None, '', None))
        lines.append(_row_line(1, p.manager))
        for g in p.goals:
            lines.append(_Line(1, g.name, '', None, '', None))
            lines.extend(_row_line(2, row) for row in g.actors)
        lines.extend(_live_line(1, a.name, a) for a in p.loose)
    lines.extend(_live_line(0, a.name, a) for a in b.loose)
    if b.history:
        lines.append(_Line(0, 'history', '', None, '', None))
        lines.extend(_row_line(1, row) for row in b.history)
    return lines


# NEW/CUR/DONE column widths — fixed, matching their header labels ('NEW'/'CUR'/'DONE').
_MAIL_WIDTHS = (len(_HEADERS[3]), len(_HEADERS[4]), len(_HEADERS[5]))


def _mail_cell(count: int, width: int, *, urgent: bool) -> str:
    # pad the plain text *before* styling — padding a styled string would count its
    # invisible ANSI escape bytes and misalign multi-digit counts against the header
    text = (str(count) if count else '·').rjust(width)
    if count == 0:
        return typer.style(text, fg=_ARCHIVED_COLOR)
    return typer.style(text, fg='yellow' if urgent else None, bold=urgent)


def _render_line(line: _Line, widths: tuple[int, int, int]) -> str:
    name_w, status_w, detail_w = widths
    name = ('  ' * line.indent + line.name).ljust(name_w)
    if line.mail is None:
        return typer.style(name, bold=True)  # a section header — no columns to align
    status = line.status.ljust(status_w)
    if line.status_color:
        status = typer.style(status, fg=line.status_color)
    detail = line.detail.ljust(detail_w)
    cells = (
        _mail_cell(line.mail.new, _MAIL_WIDTHS[0], urgent=True),
        _mail_cell(line.mail.cur, _MAIL_WIDTHS[1], urgent=False),
        _mail_cell(line.mail.done, _MAIL_WIDTHS[2], urgent=False),
    )
    return f'{name}  {status}  {detail}  {"  ".join(cells)}'


def render(b: Board) -> str:
    """The dashboard as one colorized, column-aligned block of text."""
    lines = _flatten(b)
    name_w = max([len(_HEADERS[0])] + [len('  ' * line.indent + line.name) for line in lines])
    status_w = max([len(_HEADERS[1])] + [len(line.status) for line in lines])
    detail_w = max([len(_HEADERS[2])] + [len(line.detail) for line in lines])
    widths = (name_w, status_w, detail_w)
    header = (
        _HEADERS[0].ljust(name_w),
        _HEADERS[1].ljust(status_w),
        _HEADERS[2].ljust(detail_w),
        _HEADERS[3].rjust(_MAIL_WIDTHS[0]),
        _HEADERS[4].rjust(_MAIL_WIDTHS[1]),
        _HEADERS[5].rjust(_MAIL_WIDTHS[2]),
    )
    out = [typer.style('  '.join(header), dim=True, bold=True), b.workspace]
    out.extend(_render_line(line, widths) for line in lines)
    return '\n'.join(out)
