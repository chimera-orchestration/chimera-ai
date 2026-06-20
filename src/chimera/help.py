"""Derived help index — the whole command tree in one chunk.

``ch help`` walks the live Click command tree (never a hand-kept list), so it can't
drift: every summary, metavar, option and synonym is read straight off the real
command objects. Like ``completions.py``, this is the one other place that reaches
into typer's click layer.
"""

import json
from dataclasses import asdict, dataclass
from typing import cast

from typer._click.core import Command, Context, Parameter
from typer.core import TyperGroup

# get_short_help_str truncates at 45 by default; our summaries are full one-liners.
SUMMARY_LIMIT = 100


@dataclass(frozen=True)
class HelpEntry:
    """One leaf command, fully derived from its Click command object."""

    path: str  # canonical command path, e.g. 'goal finish'
    usage: str  # positional metavars only, e.g. 'GOAL' (never '[OPTIONS]')
    summary: str  # the command's own short help
    options: tuple[str, ...]  # option signatures, e.g. '--project/-p TEXT' (for -v / json)
    synonyms: tuple[str, ...]  # other names that dispatch here, e.g. 'cleanup' (for -v / json)


def command_index(root: Command) -> list[HelpEntry]:
    """Every visible leaf command under ``root``, depth-first, derived from the tree."""
    entries: list[HelpEntry] = []
    _walk(cast(TyperGroup, root), '', None, entries)
    return entries


def _walk(group: TyperGroup, prefix: str, parent: Context | None, out: list[HelpEntry]) -> None:
    ctx = Context(group, info_name=prefix.strip() or 'ch', parent=parent)
    synonyms = _synonyms_by_target(group)
    for name in group.list_commands(ctx):
        command = group.get_command(ctx, name)
        if command is None or command.hidden:
            continue
        path = f'{prefix}{name}'
        if isinstance(command, TyperGroup):  # a group — recurse
            _walk(command, f'{path} ', ctx, out)
            continue
        child = Context(command, info_name=path, parent=ctx)
        out.append(
            HelpEntry(
                path=path,
                usage=' '.join(p for p in command.collect_usage_pieces(child) if p != '[OPTIONS]'),
                summary=command.get_short_help_str(limit=SUMMARY_LIMIT),
                options=tuple(_option(p, child) for p in command.get_params(child) if _wanted(p)),
                synonyms=synonyms.get(name, ()),
            )
        )


def _synonyms_by_target(group: TyperGroup) -> dict[str, tuple[str, ...]]:
    """Invert a group's ``{synonym: canonical}`` map to ``{canonical: (synonyms...)}``."""
    inverted: dict[str, list[str]] = {}
    for synonym, canonical in getattr(group, 'synonyms', {}).items():
        inverted.setdefault(canonical, []).append(synonym)
    return {canonical: tuple(names) for canonical, names in inverted.items()}


def _wanted(param: Parameter) -> bool:
    hidden = getattr(param, 'hidden', False)
    return param.param_type_name == 'option' and not hidden and '--help' not in param.opts


def _option(param: Parameter, ctx: Context) -> str:
    names = '/'.join(param.opts)
    return names if getattr(param, 'is_flag', False) else f'{names} {param.make_metavar(ctx)}'


def render_text(entries: list[HelpEntry], *, verbose: bool) -> str:
    """The index as aligned plain text; ``verbose`` adds each command's options + synonyms."""
    width = max((len(_signature(e)) for e in entries), default=0)
    lines: list[str] = []
    for entry in entries:
        lines.append(f'{_signature(entry):<{width}}  {entry.summary}'.rstrip())
        if verbose:
            lines.extend(f'    {option}' for option in entry.options)
            if entry.synonyms:
                lines.append(f'    (also: {", ".join(entry.synonyms)})')
    if not verbose and any(e.options or e.synonyms for e in entries):
        lines.append("ch help -v also lists each command's options & synonyms")
    return '\n'.join(lines)


def _signature(entry: HelpEntry) -> str:
    return f'{entry.path} {entry.usage}'.rstrip()


def render_json(entries: list[HelpEntry]) -> str:
    """The index as JSON — every field, synonyms included, for machine consumption."""
    return json.dumps([asdict(entry) for entry in entries], indent=2)
