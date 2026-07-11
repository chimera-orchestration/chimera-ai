import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from string import Template
from subprocess import CompletedProcess

from giterator import GitError
from giterator.testing import Repo
from testfixtures import LogCapture, Replacer, ShouldRaise, TempDir, compare
from testfixtures.loguru import LoguruSource
from testfixtures.mock import Mock

import chimera.__main__ as main
from chimera.commands import review as review_mod
from chimera.agents.registry import AgentSpec
from chimera.dry import Dry
from chimera.commands.agent import agent
from chimera.commands.review import (
    GUARDRAIL,
    _check_pr_repo,
    _default_template,
    _pr_argument,
    _pr_metadata,
    _prompt,
    _wire_upstream,
    review,
)
from chimera.config import UserError
from chimera.git import Git
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
        spec: AgentSpec = AgentSpec(),
        context: Path | None = None,
        env: Mapping[str, str] = {},
        dry: Dry = Dry(),
    ) -> None:
        calls.append((worktree, name, prompt, extra, dangerous, spec, context, env))

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
        expected=[
            (
                worktrees / 'pr-1@agent',
                'proj@pr-1@agent',
                expected_prompt,
                (),
                False,
                AgentSpec(),
                None,
                {},
            )
        ],
    )


def test_review_keys_the_context_by_the_resolved_session_name(
    tmpdir: TempDir, replace: Replacer
) -> None:
    repo, head = _cloned(tmpdir)
    worktrees = tmpdir / 'wt'
    meta = _meta(head)
    _stub_meta(replace, meta)
    calls = _stub_agent(replace)
    names: list[str] = []

    def factory(name: str) -> Path:
        names.append(name)
        return tmpdir / 'ctx.md'

    # a URL argument still keys the context by the goal gh resolves, never the raw argument
    url = 'https://github.com/o/r/pull/1'
    review(repo, worktrees, 'proj', tmpdir / 'prompts', url, context=factory)
    compare(names, expected=['proj@pr-1@agent'])
    expected_prompt = GUARDRAIL + Template(_default_template()).safe_substitute(
        PR=1, PR_URL=meta['url'], PR_TITLE=meta['title'], BASE='main', GOAL='pr-1', PROJECT='proj'
    )
    compare(
        calls,
        expected=[
            (
                worktrees / 'pr-1@agent',
                'proj@pr-1@agent',
                expected_prompt,
                (),
                False,
                AgentSpec(),
                tmpdir / 'ctx.md',
                {},
            )
        ],
    )


def test_review_logs_the_goal_refs(tmpdir: TempDir, replace: Replacer) -> None:
    repo, head = _cloned(tmpdir)
    worktrees = tmpdir / 'wt'
    _stub_meta(replace, _meta(head))
    _stub_agent(replace)
    with LogCapture(LoguruSource(('message', 'extra'), level='INFO')) as log:
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


def test_review_without_launch_stops_after_the_checkout(tmpdir: TempDir, replace: Replacer) -> None:
    repo, head = _cloned(tmpdir)
    worktrees = tmpdir / 'wt'
    _stub_meta(replace, _meta(head))
    calls = _stub_agent(replace)
    compare(
        review(repo, worktrees, 'proj', tmpdir / 'prompts', '1', into=repo, launch=False),
        expected=worktrees / 'pr-1@agent',
    )
    git = Git(repo)
    # the goal stands ready — branches on the PR head, human landed in place — but no agent ran
    compare(git.rev_parse('pr-1/agent', short=False), expected=head)
    compare(git('rev-parse', '--abbrev-ref', 'HEAD').strip(), expected='pr-1/human')
    compare(calls, expected=[])


def test_review_without_launch_refuses_agent_flags(tmpdir: TempDir) -> None:
    refused = UserError(
        '--no-agent launches no agent, so --dangerous and "-- …" have nothing to apply to.'
    )
    with ShouldRaise(refused):
        review(tmpdir / 'r', tmpdir / 'wt', 'proj', tmpdir / 'p', '1', launch=False, dangerous=True)
    with ShouldRaise(refused):
        review(tmpdir / 'r', tmpdir / 'wt', 'proj', tmpdir / 'p', '1', launch=False, extra=['-c'])


def test_review_refuses_without_an_origin(tmpdir: TempDir, replace: Replacer) -> None:
    repo = Repo.make(tmpdir / 'r')  # no remotes at all
    repo.commit_content('seed')
    worktrees = tmpdir / 'wt'
    unreached = Mock(side_effect=AssertionError('should not be reached'))
    replace.in_module(_pr_metadata, unreached, module=review_mod)  # refusal precedes any gh call
    with ShouldRaise(
        UserError(
            "project 'proj' has no origin to fetch a PR from — "
            'publish it first: ch project push <url>'
        )
    ):
        review(repo.path, worktrees, 'proj', tmpdir / 'prompts', '1')
    assert not worktrees.exists()


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
    assert '$PR_TITLE' in _default_template()  # importlib.resources found it


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
    compare(_wire_upstream(git, 1, head, 'origin/pr/1'), expected='origin/pr/1')
    compare(git.rev_parse('origin/pr/1', short=False), expected=head)


def test_wire_upstream_adds_a_refspec_when_none_configured(tmpdir: TempDir) -> None:
    origin, head = _origin_with_pr(tmpdir)
    git = Git(Repo.make(tmpdir / 'r').path)
    git('remote', 'add', 'origin', str(origin.path))
    git('config', '--unset-all', 'remote.origin.fetch')  # no fetch refspec at all
    compare(_wire_upstream(git, 1, head, 'origin/pr/1'), expected='origin/pr/1')
    compare(git.rev_parse('origin/pr/1', short=False), expected=head)


def test_wire_upstream_leaves_config_clean_when_the_pr_ref_is_missing(tmpdir: TempDir) -> None:
    repo, head = _cloned(tmpdir)
    git = Git(repo)
    before = git('config', '--get-all', 'remote.origin.fetch')
    with ShouldRaise(GitError, match='refs/pull/9/head'):
        _wire_upstream(git, 9, head, 'origin/pr/9')  # origin has no PR #9
    # the failed fetch must not have persisted a dead refspec that bricks future fetches
    compare(git('config', '--get-all', 'remote.origin.fetch'), expected=before)
    git('fetch', '--prune', 'origin')  # proves origin is still fetchable


def _repo_with_origin(tmpdir: TempDir, origin_url: str) -> Git:
    git = Git(Repo.make(tmpdir / 'r').path)
    git('remote', 'add', 'origin', origin_url)
    return git


def test_check_pr_repo_refuses_a_url_for_another_repo(tmpdir: TempDir) -> None:
    git = _repo_with_origin(tmpdir, 'https://github.com/chimera-orchestration/chimera-ai.git')
    with ShouldRaise(
        UserError(
            "PR is on simplistix/giterator, but project 'chimera' tracks "
            'chimera-orchestration/chimera-ai — run ch review from the project '
            'tracking simplistix/giterator (or pass -p).'
        )
    ):
        _check_pr_repo(git, 'https://github.com/simplistix/giterator/pull/2', 'chimera')


def test_check_pr_repo_passes_when_the_scp_origin_matches(tmpdir: TempDir) -> None:
    git = _repo_with_origin(tmpdir, 'git@github.com:simplistix/giterator.git')
    assert _check_pr_repo(git, 'https://github.com/simplistix/giterator/pull/2', 'proj') is None


def test_check_pr_repo_skips_a_local_path_origin(tmpdir: TempDir) -> None:
    git = _repo_with_origin(tmpdir, str(tmpdir / 'somewhere' / 'origin'))
    assert _check_pr_repo(git, 'https://github.com/simplistix/giterator/pull/2', 'proj') is None


def test_check_pr_repo_skips_without_an_origin(tmpdir: TempDir) -> None:
    assert _check_pr_repo(Git(Repo.make(tmpdir / 'r').path), 'https://x/o/r/pull/1', 'p') is None


def test_check_pr_repo_skips_an_unparseable_pr_url(tmpdir: TempDir) -> None:
    git = _repo_with_origin(tmpdir, 'https://github.com/o/r.git')
    assert _check_pr_repo(git, '', 'proj') is None  # number-only PR: no comparable URL


class TestPrArgument:
    def test_number_passes_through(self, tmpdir: TempDir) -> None:
        compare(_pr_argument(Git(Repo.make(tmpdir / 'r').path), '21368', 'proj'), expected='21368')

    def test_github_url_passes_through(self, tmpdir: TempDir) -> None:
        url = 'https://github.com/o/r/pull/7'
        compare(
            _pr_argument(_repo_with_origin(tmpdir, 'https://github.com/o/r.git'), url, 'proj'),
            expected=url,
        )

    def test_reviewable_url_yields_the_number(self, tmpdir: TempDir) -> None:
        git = _repo_with_origin(tmpdir, 'git@github.com:Tesseract-Energy/python_monorepo.git')
        with LogCapture(LoguruSource(('message', 'extra'), level='INFO')) as log:
            compare(
                _pr_argument(
                    git,
                    'https://reviewable.io/reviews/Tesseract-Energy/python_monorepo/21368',
                    'proj',
                ),
                expected='21368',
            )
        log.check(
            (
                'review: pr number from url',
                {
                    'url': 'https://reviewable.io/reviews/Tesseract-Energy/python_monorepo/21368',
                    'number': '21368',
                },
            ),
        )

    def test_number_needs_not_trail_the_url(self, tmpdir: TempDir) -> None:
        git = _repo_with_origin(tmpdir, 'https://github.com/o/r.git')
        compare(
            _pr_argument(git, 'https://app.graphite.dev/github/pr/o/r/123/fix-the-thing', 'proj'),
            expected='123',
        )

    def test_url_for_another_repo_refuses(self, tmpdir: TempDir) -> None:
        git = _repo_with_origin(tmpdir, 'https://github.com/o/r.git')
        url = 'https://reviewable.io/reviews/other/repo/5'
        with ShouldRaise(
            UserError(
                f"{url} doesn't name a PR of o/r, which project 'proj' tracks — "
                f'pass the PR number, or run ch review from the project tracking it (or pass -p).'
            )
        ):
            _pr_argument(git, url, 'proj')

    def test_url_with_no_number_after_the_slug_refuses(self, tmpdir: TempDir) -> None:
        git = _repo_with_origin(tmpdir, 'https://github.com/o/r.git')
        url = 'https://reviewable.io/reviews/o/r'
        with ShouldRaise(UserError, match='pass the PR number'):
            _pr_argument(git, url, 'proj')

    def test_no_origin_refuses(self, tmpdir: TempDir) -> None:
        url = 'https://reviewable.io/reviews/o/r/5'
        with ShouldRaise(
            UserError(
                f"project 'proj' has no github origin to match {url} against — "
                f'pass the PR number instead.'
            )
        ):
            _pr_argument(Git(Repo.make(tmpdir / 'r').path), url, 'proj')

    def test_local_path_origin_refuses(self, tmpdir: TempDir) -> None:
        git = _repo_with_origin(tmpdir, str(tmpdir / 'somewhere' / 'origin'))
        url = 'https://reviewable.io/reviews/o/r/5'
        with ShouldRaise(
            UserError(
                f"project 'proj' has no github origin to match {url} against — "
                f'pass the PR number instead.'
            )
        ):
            _pr_argument(git, url, 'proj')

    def test_review_routes_urls_through_extraction(self, tmpdir: TempDir) -> None:
        repo, _ = _cloned(tmpdir)  # a local-path origin: no slug to match a review-tool URL
        url = 'https://reviewable.io/reviews/o/r/1'
        with ShouldRaise(
            UserError(
                f"project 'proj' has no github origin to match {url} against — "
                f'pass the PR number instead.'
            )
        ):
            review(repo, tmpdir / 'wt', 'proj', tmpdir / 'prompts', url)


def test_wire_upstream_refuses_a_mismatched_head(tmpdir: TempDir) -> None:
    repo, head = _cloned(tmpdir)
    wrong = '0' * 40
    with ShouldRaise(
        UserError(f'PR #1: fetched origin/pr/1 is {head}, but gh reports headRefOid {wrong}')
    ):
        _wire_upstream(Git(repo), 1, wrong, 'origin/pr/1')


def _project_dir(tmpdir: TempDir, repo: Repo) -> Path:
    tmpdir.dump('project/config.yaml', {'kind': 'project', 'repo': str(repo.path)})
    os.chdir(tmpdir / 'project')  # the CLI infers the project (and its name) from cwd
    return tmpdir / 'project'


def test_review_dry_wires_nothing(tmpdir: TempDir, replace: Replacer) -> None:
    clone, head = _cloned(tmpdir)
    _stub_meta(replace, _meta(head))
    _stub_agent(replace)
    worktrees = tmpdir / 'wt'
    with LogCapture(LoguruSource(('message', 'extra'), level='INFO')) as log:
        compare(
            review(clone, worktrees, 'proj', tmpdir / 'prompts', '1', dry=Dry(True)),
            expected=worktrees / 'pr-1@agent',
        )
    git = Git(clone)
    assert not worktrees.exists()  # no worktree dir
    assert not git.ref_exists('origin/pr/1')  # tracking ref never fetched
    compare(  # and no PR refspec persisted alongside the clone's default one
        git('config', '--get-all', 'remote.origin.fetch').splitlines(),
        expected=['+refs/heads/*:refs/remotes/origin/*'],
    )
    log.check_empty()  # no refs changed, so no 'review: refs' line


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
        launch: bool = True,
        spec: AgentSpec = AgentSpec(),
        context: Callable[[str], Path | None] | None = None,
        env: Callable[[str], Mapping[str, str]] | None = None,
        dry: Dry = Dry(),
    ) -> Path:
        rendered = context('project@pr-1@agent') if context is not None else None
        stamp = env('project@pr-1@agent') if env is not None else None
        calls.append((project, pr, list(extra), dangerous, into, launch, spec, rendered, stamp))
        return worktrees / 'pr-1@agent'

    replace(target=review, container=main, name='_review', replacement=record)
    expected = Path.cwd() / 'worktrees' / 'pr-1@agent'
    command.run('review', '1', '--', '--model', 'opus').check(
        output=f'Reviewing 1 in {expected}',
        logging=action_logs(
            'review',
            'chimera.commands.review.review',
            {
                'pr': '1',
                'dangerous': False,
                'no_agent': False,
                'harness': None,
                'model': None,
                'dry': False,
                'project': None,
            },
        ),
    )
    compare(
        calls,
        expected=[
            (
                'project',
                '1',
                ['--model', 'opus'],
                False,
                Path.cwd(),
                True,
                AgentSpec(),
                None,
                {'CHIMERA_ROLE': 'agent', 'CHIMERA_ROLE_SCOPE': 'project@pr-1'},
            )
        ],
    )
    calls.clear()
    command.run('review', '1', '--no-agent').check(
        output=f'Prepared review of 1 in {expected}\n'
        f'ch agent start -g pr-1 launches an agent there; '
        f'ch review 1 runs the standard review',
        logging=action_logs(
            'review',
            'chimera.commands.review.review',
            {
                'pr': '1',
                'dangerous': False,
                'no_agent': True,
                'harness': None,
                'model': None,
                'dry': False,
                'project': None,
            },
        ),
    )
    compare(
        calls,
        expected=[
            (
                'project',
                '1',
                [],
                False,
                Path.cwd(),
                False,
                AgentSpec(),
                None,
                {'CHIMERA_ROLE': 'agent', 'CHIMERA_ROLE_SCOPE': 'project@pr-1'},
            )
        ],
    )


def _dry_review_cli(tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command):
    project = _project_dir(tmpdir, git_repo)
    calls: list[object] = []

    def record(
        repo: Path,
        worktrees: Path,
        project_name: str,
        prompts: Path,
        pr: str,
        extra: Sequence[str] = (),
        dangerous: bool = False,
        into: Path | None = None,
        launch: bool = True,
        spec: AgentSpec = AgentSpec(),
        context: Callable[[str], Path | None] | None = None,
        env: Callable[[str], Mapping[str, str]] | None = None,
        dry: Dry = Dry(),
    ) -> Path:
        if env is not None:  # the real review stamps the role through the factory on launch
            env('project@pr-1@agent')
        calls.append(dry)
        return worktrees / 'pr-1@agent'

    replace(target=review, container=main, name='_review', replacement=record)
    return project, calls


def test_review_cli_url_and_number_share_the_context_artifact(
    tmpdir: TempDir, replace: Replacer, command: Command
) -> None:
    clone, head = _cloned(tmpdir)  # review needs an origin, even under --dry
    ws = tmpdir.makedir('lycia')
    tmpdir.dump('lycia/config.yaml', {'kind': 'workspace'})
    tmpdir.dump('lycia/proj/config.yaml', {'kind': 'project', 'repo': str(clone)})
    tmpdir.write('lycia/proj/principles/style.md', 'Be terse.\n')
    os.chdir(ws / 'proj')
    _stub_meta(replace, _meta(head))
    _stub_agent(replace)
    compare(command.run('review', '1', '--dry').return_code, expected=0)
    [artifact] = (ws / 'logs' / 'context').iterdir()
    assert artifact.name.startswith('proj@pr-1@agent-')
    url_run = command.run('review', 'https://github.com/o/r/pull/1', '--dry')
    compare(url_run.return_code, expected=0)
    # the URL form lands on the very same artifact the number form rendered
    compare(sorted((ws / 'logs' / 'context').iterdir()), expected=[artifact])


def test_review_cli_dry_with_packaged_template(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    project, calls = _dry_review_cli(tmpdir, git_repo, replace, command)
    expected = Path.cwd() / 'worktrees' / 'pr-1@agent'
    command.run('review', '1', '--dry').check(
        output='\n'.join(
            [
                f'Would review 1 in {expected}',
                'harness: claude',
                'role: agent (scope: project@pr-1)',
                'prompt: review template (packaged default) + guardrail',
                'context: (none)',
            ]
        ),
        logging=action_logs(
            'review',
            'chimera.commands.review.review',
            {
                'pr': '1',
                'dangerous': False,
                'no_agent': False,
                'harness': None,
                'model': None,
                'dry': True,
                'project': None,
            },
        ),
    )
    compare(calls, expected=[Dry(True)])


def test_review_cli_dry_names_the_project_template(
    tmpdir: TempDir, git_repo: Repo, replace: Replacer, command: Command
) -> None:
    project, calls = _dry_review_cli(tmpdir, git_repo, replace, command)
    override = project / 'prompts' / 'review.md'
    override.parent.mkdir(parents=True)
    override.write_text('Review $PR carefully.\n')
    expected = Path.cwd() / 'worktrees' / 'pr-1@agent'
    command.run('review', '1', '--dry').check(
        output='\n'.join(
            [
                f'Would review 1 in {expected}',
                'harness: claude',
                'role: agent (scope: project@pr-1)',
                f'prompt: review template ({Path.cwd() / "prompts" / "review.md"}) + guardrail',
                'context: (none)',
            ]
        ),
        logging=action_logs(
            'review',
            'chimera.commands.review.review',
            {
                'pr': '1',
                'dangerous': False,
                'no_agent': False,
                'harness': None,
                'model': None,
                'dry': True,
                'project': None,
            },
        ),
    )
    compare(calls, expected=[Dry(True)])
