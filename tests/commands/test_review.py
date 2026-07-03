import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from string import Template
from subprocess import CompletedProcess

from giterator import Git
from giterator.testing import Repo
from testfixtures import LogCapture, Replacer, ShouldRaise, TempDir, compare
from testfixtures.loguru import LoguruSource

import chimera.__main__ as main
from chimera.commands import review as review_mod
from chimera.commands.agent import agent
from chimera.commands.review import (
    GUARDRAIL,
    _default_template,
    _pr_metadata,
    _prompt,
    _wire_upstream,
    review,
)
from chimera.config import UserError
from tests.cli import Command, action_logs


def _origin_with_pr(tmpdir: TempDir) -> tuple[Repo, str]:
    """An origin whose PR #1 head lives only under ``refs/pull/1/head`` (branch deleted)."""
    origin = Repo.make(tmpdir / 'origin')
    origin.commit_content('seed')
    origin('checkout', '-q', '-b', 'feature')
    head = origin.commit_content('pr-work', short=False)
    origin('checkout', '-q', 'main')
    origin('update-ref', 'refs/pull/1/head', head)
    origin('branch', '-q', '-D', 'feature')  # the head survives only as the PR ref
    return origin, head


def _meta(head: str) -> dict[str, object]:
    return {
        'number': 1,
        'headRefOid': head,
        'baseRefName': 'main',
        'title': 'Fix the thing',
        'url': 'https://github.com/o/r/pull/1',
        'isCrossRepository': False,
        'state': 'OPEN',
        'headRepositoryOwner': {'login': 'o'},
    }


def _stub_meta(replace: Replacer, meta: dict[str, object]) -> None:
    replace.in_module(_pr_metadata, lambda repo, pr: meta, module=review_mod)


def _stub_agent(replace: Replacer) -> list[object]:
    calls: list[object] = []

    def record(
        worktree: Path,
        name: str,
        prompt: str | None = None,
        extra: Sequence[str] = (),
        dangerous: bool = False,
    ) -> None:
        calls.append((worktree, name, prompt, extra, dangerous))

    replace.in_module(agent, record, module=review_mod)
    return calls


def _cloned(tmpdir: TempDir) -> tuple[Path, str]:
    origin, head = _origin_with_pr(tmpdir)
    Git.clone(origin.path, tmpdir / 'clone')
    return tmpdir / 'clone', head


def test_review_builds_the_goal_tracking_the_pr_and_launches(
    tmpdir: TempDir, replace: Replacer
) -> None:
    repo, head = _cloned(tmpdir)
    worktrees = tmpdir / 'wt'
    meta = _meta(head)
    _stub_meta(replace, meta)
    calls = _stub_agent(replace)
    compare(
        review(repo, worktrees, 'proj', tmpdir / 'prompts', '1'),
        expected=worktrees / 'pr-1@agent',
    )
    git = Git(repo)
    # both actor branches sit on the verified PR head…
    compare(git.rev_parse('pr-1/agent', short=False), expected=head)
    compare(git.rev_parse('pr-1/human', short=False), expected=head)
    # …and track the PR
    compare(
        git('rev-parse', '--abbrev-ref', 'pr-1/agent@{upstream}').strip(), expected='origin/pr/1'
    )
    compare(
        git('rev-parse', '--abbrev-ref', 'pr-1/human@{upstream}').strip(), expected='origin/pr/1'
    )
    # the agent's worktree is checked out on the PR head, launched on the guardrailed prompt
    compare(Git(worktrees / 'pr-1@agent')('rev-parse', 'HEAD').strip(), expected=head)
    expected_prompt = GUARDRAIL + Template(_default_template()).safe_substitute(
        PR=1, PR_URL=meta['url'], PR_TITLE=meta['title'], BASE='main', GOAL='pr-1', PROJECT='proj'
    )
    compare(
        calls,
        expected=[(worktrees / 'pr-1@agent', 'proj@pr-1@agent', expected_prompt, (), False)],
    )


def test_review_logs_the_goal_refs(tmpdir: TempDir, replace: Replacer) -> None:
    repo, head = _cloned(tmpdir)
    worktrees = tmpdir / 'wt'
    _stub_meta(replace, _meta(head))
    _stub_agent(replace)
    with LogCapture(LoguruSource(('message', 'extra'))) as log:
        review(repo, worktrees, 'proj', tmpdir / 'prompts', '1')
    log.check(
        (
            'review: refs',
            {
                'goal': 'pr-1',
                'git': {'before': {}, 'after': {'pr-1/human': head, 'pr-1/agent': head}},
                'worktree': str(worktrees / 'pr-1@agent'),
            },
        ),
    )


def test_review_is_idempotent(tmpdir: TempDir, replace: Replacer) -> None:
    repo, head = _cloned(tmpdir)
    worktrees = tmpdir / 'wt'
    _stub_meta(replace, _meta(head))
    _stub_agent(replace)
    review(repo, worktrees, 'proj', tmpdir / 'prompts', '1')
    git = Git(repo)
    branches, fetch = git.branches(), git('config', '--get-all', 'remote.origin.fetch')
    compare(
        review(repo, worktrees, 'proj', tmpdir / 'prompts', '1'),
        expected=worktrees / 'pr-1@agent',
    )
    compare(git.branches(), expected=branches)  # no new branches on a re-run
    compare(git('config', '--get-all', 'remote.origin.fetch'), expected=fetch)  # refspec added once


def test_review_lands_the_human_branch_in_place(tmpdir: TempDir, replace: Replacer) -> None:
    repo, head = _cloned(tmpdir)
    _stub_meta(replace, _meta(head))
    _stub_agent(replace)
    review(repo, tmpdir / 'wt', 'proj', tmpdir / 'prompts', '1', into=repo)
    compare(Git(repo)('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='pr-1/human')


def test_prompt_prefers_a_project_override(tmpdir: TempDir) -> None:
    (tmpdir.makedir('prompts') / 'review.md').write_text('Custom review of #$PR in $PROJECT.\n')
    compare(
        _prompt(tmpdir / 'prompts', _meta('deadbeef'), 'pr-1', 'proj'),
        expected=GUARDRAIL + 'Custom review of #1 in proj.\n',
    )


def test_prompt_uses_the_packaged_default_without_an_override(tmpdir: TempDir) -> None:
    meta = _meta('deadbeef')
    compare(
        _prompt(tmpdir / 'absent', meta, 'pr-1', 'proj'),
        expected=GUARDRAIL
        + Template(_default_template()).safe_substitute(
            PR=1,
            PR_URL=meta['url'],
            PR_TITLE=meta['title'],
            BASE='main',
            GOAL='pr-1',
            PROJECT='proj',
        ),
    )


def test_default_template_ships_as_package_data() -> None:
    compare('$PR_TITLE' in _default_template(), expected=True)  # importlib.resources found it


def test_pr_metadata_parses_gh_json(tmpdir: TempDir, replace: Replacer) -> None:
    replace.in_module(
        subprocess.run,
        lambda *a, **k: CompletedProcess(a, 0, stdout='{"number": 7, "title": "t"}', stderr=''),
    )
    compare(_pr_metadata(tmpdir.path, '7'), expected={'number': 7, 'title': 't'})


def test_pr_metadata_raises_on_gh_failure(tmpdir: TempDir, replace: Replacer) -> None:
    replace.in_module(
        subprocess.run,
        lambda *a, **k: CompletedProcess(a, 1, stdout='', stderr='no pull requests found'),
    )
    with ShouldRaise(UserError('gh pr view 999 failed: no pull requests found')):
        _pr_metadata(tmpdir.path, '999')


def test_wire_upstream_verifies_against_head_oid(tmpdir: TempDir) -> None:
    repo, head = _cloned(tmpdir)
    git = Git(repo)
    compare(_wire_upstream(git, 1, head), expected='origin/pr/1')
    compare(git.rev_parse('origin/pr/1', short=False), expected=head)


def test_wire_upstream_adds_a_refspec_when_none_configured(tmpdir: TempDir) -> None:
    origin, head = _origin_with_pr(tmpdir)
    git = Git(Repo.make(tmpdir / 'r').path)
    git('remote', 'add', 'origin', str(origin.path))
    git('config', '--unset-all', 'remote.origin.fetch')  # no fetch refspec at all
    compare(_wire_upstream(git, 1, head), expected='origin/pr/1')
    compare(git.rev_parse('origin/pr/1', short=False), expected=head)


def test_wire_upstream_refuses_a_mismatched_head(tmpdir: TempDir) -> None:
    repo, head = _cloned(tmpdir)
    wrong = '0' * 40
    with ShouldRaise(
        UserError(f'PR #1: fetched origin/pr/1 is {head}, but gh reports headRefOid {wrong}')
    ):
        _wire_upstream(Git(repo), 1, wrong)


def _project_dir(tmpdir: TempDir, repo: Repo) -> Path:
    tmpdir.dump('project/config.yaml', {'kind': 'project', 'repo': str(repo.path)})
    os.chdir(tmpdir / 'project')  # the CLI infers the project (and its name) from cwd
    return tmpdir / 'project'


def test_review_cli(tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command) -> None:
    _project_dir(tmpdir, git_repo)
    calls: list[object] = []

    def record(
        repo: Path,
        worktrees: Path,
        project: str,
        prompts: Path,
        pr: str,
        extra: Sequence[str] = (),
        dangerous: bool = False,
        into: Path | None = None,
    ) -> Path:
        calls.append((project, pr, list(extra), dangerous, into))
        return worktrees / 'pr-1@agent'

    replace(target=review, container=main, name='_review', replacement=record)
    expected = Path.cwd() / 'worktrees' / 'pr-1@agent'
    command.run('review', '1', '--', '--model', 'opus').check(
        output=f'Reviewing 1 in {expected}',
        logging=action_logs(
            'review',
            'chimera.commands.review.review',
            {'pr': '1', 'dangerous': False, 'project': None},
        ),
    )
    compare(calls, expected=[('project', '1', ['--model', 'opus'], False, Path.cwd())])
