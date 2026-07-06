from hashlib import sha256
from pathlib import Path

from testfixtures import LogCapture, TempDir, compare

from chimera.agents.context import KNOWLEDGE_HINT, materialize, render, role_context
from chimera.config import ProjectConfig
from chimera.context import Project


def _project(tmpdir: TempDir, workspace: Path, name: str = 'proj') -> Project:
    directory = workspace / name
    directory.mkdir(parents=True, exist_ok=True)
    tmpdir.dump(directory / 'config.yaml', {'kind': 'project', 'repo': '/r'})
    return Project(directory, ProjectConfig(kind='project', repo=Path('/r')))


def test_render_nothing_when_no_sources(tmpdir: TempDir) -> None:
    ws = tmpdir.makedir('lycia')
    compare(render(ws, _project(tmpdir, ws)), expected='')


def test_render_nothing_when_no_axes() -> None:
    compare(render(None, None), expected='')


def test_render_inlines_workspace_and_project_principles(tmpdir: TempDir) -> None:
    ws = tmpdir.makedir('lycia')
    tmpdir.write(ws / 'principles' / 'verify.md', 'Verify before done.\n')
    project = _project(tmpdir, ws)
    tmpdir.write(project.dir / 'principles' / 'style.md', 'Match house style.\n')
    compare(
        render(ws, project),
        expected='# Principles\n\nVerify before done.\n\nMatch house style.',
    )


def test_render_indexes_knowledge_without_inlining(tmpdir: TempDir) -> None:
    ws = tmpdir.makedir('lycia')
    tmpdir.write(ws / 'knowledge' / 'tmux.md', 'lots of tmux detail\n')
    project = _project(tmpdir, ws)
    tmpdir.write(project.dir / 'knowledge' / 'testfixtures.md', 'lots of detail\n')
    text = render(ws, project)
    compare(
        text,
        expected='\n'.join(
            [
                '# Knowledge index',
                KNOWLEDGE_HINT,
                f'- tmux: {(ws / "knowledge" / "tmux.md").resolve()}',
                f'- proj/testfixtures: {(project.dir / "knowledge" / "testfixtures.md").resolve()}',
            ]
        ),
    )


def test_render_unpinned_scope_indexes_every_project(tmpdir: TempDir) -> None:
    ws = tmpdir.makedir('lycia')
    alpha, beta = _project(tmpdir, ws, 'alpha'), _project(tmpdir, ws, 'beta')
    tmpdir.write(alpha.dir / 'knowledge' / 'a.md', 'a\n')
    tmpdir.write(beta.dir / 'knowledge' / 'b.md', 'b\n')
    compare(
        render(ws, None),
        expected='\n'.join(
            [
                '# Knowledge index',
                KNOWLEDGE_HINT,
                f'- alpha/a: {(alpha.dir / "knowledge" / "a.md").resolve()}',
                f'- beta/b: {(beta.dir / "knowledge" / "b.md").resolve()}',
            ]
        ),
    )


def test_render_project_without_workspace(tmpdir: TempDir) -> None:
    project = _project(tmpdir, tmpdir.path)
    tmpdir.write(project.dir / 'principles' / 'p.md', 'A principle.\n')
    compare(render(None, project), expected='# Principles\n\nA principle.')


def test_render_principles_and_knowledge_sections_join(tmpdir: TempDir) -> None:
    ws = tmpdir.makedir('lycia')
    tmpdir.write(ws / 'principles' / 'p.md', 'A principle.\n')
    tmpdir.write(ws / 'knowledge' / 'k.md', 'detail\n')
    compare(
        render(ws, None),
        expected='# Principles\n\nA principle.\n\n# Knowledge index\n'
        f'{KNOWLEDGE_HINT}\n- k: {(ws / "knowledge" / "k.md").resolve()}',
    )


def test_materialize_writes_content_addressed_file_and_logs(
    tmpdir: TempDir, full_logs: LogCapture
) -> None:
    ws = tmpdir.makedir('lycia')
    text = '# Principles\n\nVerify.'
    digest = sha256(text.encode()).hexdigest()
    path = materialize(ws, 'proj@g@agent', text)
    expected = ws / 'logs' / 'context' / f'proj@g@agent-{digest[:8]}.md'
    compare(path, expected=expected)
    compare(expected.read_text(), expected=text)
    full_logs.check(
        {
            'level': 'INFO',
            'session': 'proj@g@agent',
            'path': str(expected),
            'sha256': digest,
            'message': 'context: rendered',
        }
    )


def test_materialize_is_idempotent_for_identical_content(tmpdir: TempDir) -> None:
    ws = tmpdir.makedir('lycia')
    first = materialize(ws, 'n', 'same text')
    second = materialize(ws, 'n', 'same text')
    compare(second, expected=first)  # content-addressed: a re-run lands on the same artifact
    tmpdir.compare([first.name if first else '?'], path=ws / 'logs' / 'context')


def test_materialize_sanitizes_a_url_bearing_name(tmpdir: TempDir) -> None:
    ws = tmpdir.makedir('lycia')
    path = materialize(ws, 'proj@pr-https://github.com/o/r/pull/1@agent', 'text')
    assert path is not None
    assert '/' not in path.name


def test_materialize_none_when_nothing_to_inject(tmpdir: TempDir, full_logs: LogCapture) -> None:
    ws = tmpdir.makedir('lycia')
    assert materialize(ws, 'n', '') is None
    compare((ws / 'logs').exists(), expected=False)  # no file either
    full_logs.check()  # and no log line: nothing was rendered


def test_role_context_inlines_directives(tmpdir: TempDir) -> None:
    ws = tmpdir.makedir('lycia')
    tmpdir.write(ws / 'roles' / 'captain' / 'a.md', 'Direct the work.\n')
    tmpdir.write(ws / 'roles' / 'captain' / 'b.md', 'Never push to main.\n')
    compare(
        role_context(ws, 'captain', 'pegasus'),
        expected='# Role: captain\n\n'
        'You are pegasus, the captain of the lycia workspace.\n\n'
        'Direct the work.\n\nNever push to main.',
    )


def test_role_context_without_directives_still_introduces(tmpdir: TempDir) -> None:
    ws = tmpdir.makedir('lycia')
    compare(
        role_context(ws, 'captain', 'pegasus'),
        expected='# Role: captain\n\nYou are pegasus, the captain of the lycia workspace.',
    )
