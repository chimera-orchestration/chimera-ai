import json

import typer
from testfixtures import Command, compare
from typer.main import get_command

from chimera.__main__ import app
from chimera.help import HelpEntry, command_index, render_json, render_text


def _index() -> list[HelpEntry]:
    return command_index(get_command(app))


def _by_path() -> dict[str, HelpEntry]:
    return {entry.path: entry for entry in _index()}


def test_every_command_has_a_summary() -> None:
    # the index is derived, so a new command with no help= would surface here as blank
    compare([entry.path for entry in _index() if not entry.summary], expected=[])


def test_finish_entry_is_fully_derived() -> None:
    compare(
        _by_path()['goal finish'],
        expected=HelpEntry(
            path='goal finish',
            usage='GOAL',
            summary="Remove a goal's worktrees and branches.",
            options=('--force', '--offline', '--project/-p TEXT'),
            synonyms=('cleanup',),
        ),
    )


def test_help_lists_itself() -> None:
    compare(
        _by_path()['help'],
        expected=HelpEntry(
            path='help',
            usage='',
            summary='List every command in one chunk (derived from the live tree).',
            options=('--verbose/-v', '--json'),
            synonyms=(),
        ),
    )


def test_hidden_commands_are_excluded() -> None:
    scratch = typer.Typer()

    @scratch.command()
    def visible() -> None: ...

    @scratch.command(hidden=True)
    def secret() -> None: ...

    compare([e.path for e in command_index(get_command(scratch))], expected=['visible'])


def test_groups_are_not_entries() -> None:
    # only leaf commands appear; the 'goal'/'project'/… groups are walked through
    compare({'goal', 'project', 'worktree', 'agent'} & {e.path for e in _index()}, expected=set())


def test_text_default_omits_options_and_synonyms() -> None:
    text = render_text(_index(), verbose=False)
    compare('goal finish' in text, expected=True)
    compare('--force' in text, expected=False)
    compare('cleanup' in text, expected=False)


def test_text_default_signposts_verbose() -> None:
    compare(
        render_text(_index(), verbose=False).endswith(
            "ch help -v also lists each command's options & synonyms"
        ),
        expected=True,
    )


def test_text_verbose_shows_options_and_synonyms() -> None:
    text = render_text(_index(), verbose=True)
    compare('    --force' in text, expected=True)
    compare('    (also: cleanup)' in text, expected=True)
    compare('also lists' in text, expected=False)  # no signpost when nothing is hidden


def test_json_includes_synonyms() -> None:
    finish = next(e for e in json.loads(render_json(_index())) if e['path'] == 'goal finish')
    compare(finish['synonyms'], expected=['cleanup'])


def test_help_cli(command: Command) -> None:
    command.run('help').check(
        output=render_text(_index(), verbose=False), logging=[('INFO', 'help')]
    )


def test_help_cli_verbose(command: Command) -> None:
    command.run('help', '-v').check(
        output=render_text(_index(), verbose=True), logging=[('INFO', 'help')]
    )


def test_help_cli_json(command: Command) -> None:
    command.run('help', '--json').check(output=render_json(_index()), logging=[('INFO', 'help')])
