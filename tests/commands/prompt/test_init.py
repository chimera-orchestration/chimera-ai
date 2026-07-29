from pathlib import Path

from giterator.testing import Repo
from testfixtures import LogCapture, ShouldRaise, TempDir, compare
from testfixtures.loguru import LoguruSource

from chimera.commands.prompt import PACKAGED, Prompt
from chimera.commands.prompt.init import init
from chimera.config import UserError
from tests.cli import Command, action_logs
from tests.commands.prompt.test_prompt import project_dir


def test_init_copies_the_packaged_template(tmpdir: TempDir) -> None:
    with LogCapture(LoguruSource(('message', 'extra'), level='INFO')) as log:
        prompt, created = init(tmpdir / 'prompts', 'review')
    assert created
    compare(prompt, expected=Prompt('review', tmpdir / 'prompts' / 'review.md', overridden=True))
    compare(prompt.text, expected=(PACKAGED / 'review.md').read_text())
    log.check(
        (
            'prompt init',
            {'path': str(tmpdir / 'prompts' / 'review.md'), 'template': 'review'},
        )
    )


def test_init_never_clobbers_an_existing_override(tmpdir: TempDir) -> None:
    existing = tmpdir.makedir('prompts') / 'review.md'
    existing.write_text('Mine, hard won.\n')
    prompt, created = init(tmpdir / 'prompts', 'review')
    assert not created
    compare(prompt, expected=Prompt('review', existing, overridden=True))
    compare(existing.read_text(), expected='Mine, hard won.\n')


def test_init_refuses_an_unknown_template(tmpdir: TempDir) -> None:
    with ShouldRaise(UserError('no prompt template named plan — there is pr, review')):
        init(tmpdir / 'prompts', 'plan')
    assert not (tmpdir / 'prompts').exists()


def test_prompt_init_cli(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    project_dir(tmpdir, git_repo)
    copy = Path.cwd() / 'prompts' / 'pr.md'  # cwd: the project as the CLI resolves it
    start, end = action_logs(
        'prompt init', 'chimera.commands.prompt.init.init', {'name': 'pr', 'project': None}
    )
    copied = {'level': 'INFO', 'path': str(copy), 'template': 'pr', 'message': 'prompt init'}
    command.run('prompt', 'init', 'pr').check(
        output=f'Created {copy}', logging=[start, copied, end]
    )
    command.run('prompt', 'init', 'pr').check(  # idempotent, and nothing copied the second time
        output=f'Already yours: {copy}', logging=[start, end]
    )
