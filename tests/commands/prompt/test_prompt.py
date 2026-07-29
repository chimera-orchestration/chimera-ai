import os
from pathlib import Path
from string import Template

from giterator.testing import Repo
from testfixtures import ShouldRaise, TempDir, compare

from chimera.commands.prompt import HOLES, PACKAGED, Prompt, names, resolve
from chimera.config import UserError
from tests.cli import Command, action_logs


def project_dir(tmpdir: TempDir, repo: Repo) -> Path:
    tmpdir.dump('project/config.yaml', {'kind': 'project', 'repo': str(repo.path)})
    os.chdir(tmpdir / 'project')  # the CLI infers the project (and its prompts dir) from cwd
    return tmpdir / 'project'


class TestNames:
    def test_derived_from_the_packaged_templates(self) -> None:
        compare(names(), expected=['pr', 'review'])

    def test_every_name_has_its_holes_declared(self) -> None:
        compare(sorted(HOLES), expected=names())

    def test_every_hole_a_packaged_template_uses_is_declared(self) -> None:
        # the reverse doesn't hold: a hole the renderer offers ($SOURCE) may go unused by
        # the packaged text and still be there for a project's own template to reach for
        for name in names():
            text = (PACKAGED / f'{name}.md').read_text()
            compare(
                sorted(set(Template(text).get_identifiers()) - {h.name for h in HOLES[name]}),
                expected=[],
                prefix=f'{name} uses undeclared holes',
            )


class TestResolve:
    def test_packaged_without_an_override(self, tmpdir: TempDir) -> None:
        compare(
            resolve(tmpdir / 'prompts', 'review'),
            expected=Prompt('review', PACKAGED / 'review.md', overridden=False),
        )

    def test_the_projects_copy_when_there_is_one(self, tmpdir: TempDir) -> None:
        override = tmpdir.makedir('prompts') / 'review.md'
        override.write_text('Ours.\n')
        prompt = resolve(tmpdir / 'prompts', 'review')
        compare(prompt, expected=Prompt('review', override, overridden=True))
        compare(prompt.text, expected='Ours.\n')

    def test_a_directory_of_that_name_is_not_an_override(self, tmpdir: TempDir) -> None:
        tmpdir.makedir('prompts/review.md')
        compare(
            resolve(tmpdir / 'prompts', 'review'),
            expected=Prompt('review', PACKAGED / 'review.md', overridden=False),
        )

    def test_an_unknown_name_refuses_with_the_known_ones(self, tmpdir: TempDir) -> None:
        with ShouldRaise(UserError('no prompt template named plan — there is pr, review')):
            resolve(tmpdir / 'prompts', 'plan')


class TestHole:
    def test_a_launch_filled_hole_renders_as_a_placeholder(self) -> None:
        (pr,) = (hole for hole in HOLES['review'] if hole.name == 'PR')
        compare(pr.value, expected='<the pull request number>')


def test_prompt_show_cli(tmpdir: TempDir, git_repo: Repo, command: Command) -> None:
    project_dir(tmpdir, git_repo)
    prompts = Path.cwd() / 'prompts'  # cwd, so the expectation matches the resolved project
    prompts.mkdir()
    (prompts / 'review.md').write_text('Review #$PR, please\n')
    command.run('prompt', 'show', 'review').check(
        output=f'source: {prompts / "review.md"}\n'
        f'\n'
        f'Review #$PR, please\n'
        f'\n'
        f'substitutions:\n'
        f'  $PR = <the pull request number>\n'
        f"  $PR_URL = <the pull request's url>\n"
        f"  $PR_TITLE = <the pull request's title>\n"
        f'  $BASE = <the branch the PR targets>\n'
        f'  $GOAL = <the goal the review runs as>\n'
        f'  $PROJECT = <the project name>',
        logging=action_logs(
            'prompt show', 'chimera.commands.prompt.resolve', {'name': 'review', 'project': None}
        ),
    )


def test_prompt_show_cli_points_at_init_for_a_packaged_template(
    tmpdir: TempDir, git_repo: Repo, command: Command
) -> None:
    project_dir(tmpdir, git_repo)
    holes = '\n'.join(f'  ${hole.name} = {hole.value}' for hole in HOLES['pr'])
    command.run('prompt', 'show', 'pr').check(
        output=f'source: {PACKAGED / "pr.md"} (packaged)\n'
        f'  ch prompt init pr copies it into the project to edit\n'
        f'\n{(PACKAGED / "pr.md").read_text().rstrip()}\n'
        f'\nsubstitutions:\n{holes}',
        logging=action_logs(
            'prompt show', 'chimera.commands.prompt.resolve', {'name': 'pr', 'project': None}
        ),
    )
