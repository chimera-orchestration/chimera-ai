from pathlib import Path

from giterator.testing import Repo
from testfixtures import TempDir, compare

from chimera.commands.prompt import PACKAGED, Prompt
from chimera.commands.prompt.ls import prompts
from tests.cli import Command, action_logs
from tests.commands.prompt.test_prompt import project_dir


def test_prompts_resolves_every_template(tmpdir: TempDir) -> None:
    override = tmpdir.makedir('prompts') / 'pr.md'
    override.write_text('Ours.\n')
    compare(
        prompts(tmpdir / 'prompts'),
        expected=[
            Prompt('pr', override, overridden=True),
            Prompt('review', PACKAGED / 'review.md', overridden=False),
        ],
    )


def test_prompt_ls_cli(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    project_dir(tmpdir, git_repo)
    prompts = Path.cwd() / 'prompts'  # cwd, so the expectation matches the resolved project
    prompts.mkdir()
    (prompts / 'review.md').write_text('Ours.\n')
    command.run('prompt', 'ls').check(
        output=f'pr       {PACKAGED / "pr.md"} (packaged)\nreview   {prompts / "review.md"}',
        logging=action_logs('prompt ls', 'chimera.commands.prompt.ls.prompts', {'project': None}),
    )
