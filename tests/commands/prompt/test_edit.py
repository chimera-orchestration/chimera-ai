import sys
from pathlib import Path

from giterator.testing import Repo
from testfixtures import Replacer, ShouldRaise, TempDir, compare, not_there

from chimera.commands.prompt import PACKAGED, Prompt
from chimera.commands.prompt.edit import edit
from chimera.config import UserError
from tests.cli import Command, action_logs
from tests.commands.prompt.test_prompt import project_dir

# an "editor" that appends to whatever file it is handed, so a real subprocess proves the
# path reaches the command it runs
APPENDER = (
    f'{sys.executable} -c '
    "'import sys,pathlib; p=pathlib.Path(sys.argv[1]); p.write_text(p.read_text()+\"edited\\n\")'"
)


def test_edit_creates_then_opens_the_projects_copy(tmpdir: TempDir) -> None:
    compare(
        edit(tmpdir / 'prompts', 'review', APPENDER),
        expected=Prompt('review', tmpdir / 'prompts' / 'review.md', overridden=True),
    )
    compare(
        (tmpdir / 'prompts' / 'review.md').read_text(),
        expected=(PACKAGED / 'review.md').read_text() + 'edited\n',
    )


def test_edit_opens_an_existing_override_in_place(tmpdir: TempDir) -> None:
    existing = tmpdir.makedir('prompts') / 'review.md'
    existing.write_text('Mine.\n')
    edit(tmpdir / 'prompts', 'review', APPENDER)
    compare(existing.read_text(), expected='Mine.\nedited\n')


def test_edit_falls_back_to_visual_then_editor(tmpdir: TempDir, replace: Replacer) -> None:
    replace.in_environ('EDITOR', APPENDER)
    replace.in_environ('VISUAL', not_there)
    edit(tmpdir / 'prompts', 'review')
    assert (tmpdir / 'prompts' / 'review.md').read_text().endswith('edited\n')


def test_edit_refuses_without_an_editor(tmpdir: TempDir, replace: Replacer) -> None:
    replace.in_environ('EDITOR', not_there)
    replace.in_environ('VISUAL', not_there)
    with ShouldRaise(
        UserError(
            f'no editor to open {tmpdir / "prompts" / "review.md"} with — '
            f'set $VISUAL or $EDITOR, or pass --editor'
        )
    ):
        edit(tmpdir / 'prompts', 'review')
    # the copy is still made: the refusal is only about opening it
    assert (tmpdir / 'prompts' / 'review.md').exists()


def test_edit_reports_an_editor_that_failed(tmpdir: TempDir) -> None:
    failing = f'{sys.executable} -c "import sys; sys.exit(1)"'
    with ShouldRaise(
        UserError(f'{failing} exited non-zero; {tmpdir / "prompts" / "review.md"} may be unedited')
    ):
        edit(tmpdir / 'prompts', 'review', failing)


def test_prompt_edit_cli(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    project_dir(tmpdir, git_repo)
    start, end = action_logs(
        'prompt edit',
        'chimera.commands.prompt.edit.edit',
        {'name': 'review', 'editor': APPENDER, 'project': None},
    )
    path = Path.cwd() / 'prompts' / 'review.md'  # cwd: the project as the CLI resolves it
    command.run('prompt', 'edit', 'review', '--editor', APPENDER).check(
        output=f'Edited {path}',
        logging=[
            start,
            {'level': 'INFO', 'path': str(path), 'template': 'review', 'message': 'prompt init'},
            {'level': 'INFO', 'path': str(path), 'editor': APPENDER, 'message': 'prompt edit'},
            end,
        ],
    )
    assert path.read_text().endswith('edited\n')
