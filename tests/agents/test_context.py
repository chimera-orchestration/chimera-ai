from hashlib import sha256
from pathlib import Path

from testfixtures import LogCapture, TempDir, compare

from chimera.agents.context import KNOWLEDGE_HINT, Rendered, Source, assemble, materialize
from chimera.config import ProjectConfig
from chimera.context import Project


def _project(tmpdir: TempDir, workspace: Path, name: str = 'proj') -> Project:
    directory = workspace / name
    directory.mkdir(parents=True, exist_ok=True)
    tmpdir.dump(directory / 'config.yaml', {'kind': 'project', 'repo': '/r'})
    return Project(directory, ProjectConfig(kind='project', repo=Path('/r')))


def _attributed(path: Path, layer: str, content: str) -> str:
    return f'<!-- {path.resolve()} ({layer}) -->\n{content}'


class TestAssemble:
    def test_role_section_alone_when_no_sources(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('lycia')
        rendered = assemble(ws, _project(tmpdir, ws), 'agent', 'You are the agent for g.')
        compare(rendered.text, expected='# Role: agent\n\nYou are the agent for g.')

    def test_inlines_workspace_and_project_principles(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('lycia')
        tmpdir.write(ws / 'principles' / 'verify.md', 'Verify before done.\n')
        project = _project(tmpdir, ws)
        tmpdir.write(project.dir / 'principles' / 'style.md', 'Match house style.\n')
        compare(
            assemble(ws, project, 'agent', 'intro').text,
            expected='# Role: agent\n\nintro\n\n# Principles\n\n'
            + _attributed(ws / 'principles' / 'verify.md', 'workspace', 'Verify before done.')
            + '\n\n'
            + _attributed(project.dir / 'principles' / 'style.md', 'project', 'Match house style.'),
        )

    def test_indexes_knowledge_without_inlining(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('lycia')
        tmpdir.write(ws / 'knowledge' / 'tmux.md', 'lots of tmux detail\n')
        project = _project(tmpdir, ws)
        tmpdir.write(project.dir / 'knowledge' / 'testfixtures.md', 'lots of detail\n')
        compare(
            assemble(ws, project, 'agent', 'intro').text,
            expected='\n'.join(
                [
                    '# Role: agent\n\nintro\n\n# Knowledge index',
                    KNOWLEDGE_HINT,
                    f'- tmux: {(ws / "knowledge" / "tmux.md").resolve()}',
                    f'- proj/testfixtures: '
                    f'{(project.dir / "knowledge" / "testfixtures.md").resolve()}',
                ]
            ),
        )

    def test_unpinned_scope_indexes_every_project(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('lycia')
        alpha, beta = _project(tmpdir, ws, 'alpha'), _project(tmpdir, ws, 'beta')
        tmpdir.write(alpha.dir / 'knowledge' / 'a.md', 'a\n')
        tmpdir.write(beta.dir / 'knowledge' / 'b.md', 'b\n')
        compare(
            assemble(ws, None, 'captain', 'intro').text,
            expected='\n'.join(
                [
                    '# Role: captain\n\nintro\n\n# Knowledge index',
                    KNOWLEDGE_HINT,
                    f'- alpha/a: {(alpha.dir / "knowledge" / "a.md").resolve()}',
                    f'- beta/b: {(beta.dir / "knowledge" / "b.md").resolve()}',
                ]
            ),
        )

    def test_directives_sorted_within_a_layer(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('lycia')
        tmpdir.write(ws / 'roles' / 'captain' / 'b.md', 'Never push to main.\n')
        tmpdir.write(ws / 'roles' / 'captain' / 'a.md', 'Direct the work.\n')
        compare(
            assemble(ws, None, 'captain', 'You are pegasus.').text,
            expected='# Role: captain\n\nYou are pegasus.\n\n'
            + _attributed(ws / 'roles' / 'captain' / 'a.md', 'workspace', 'Direct the work.')
            + '\n\n'
            + _attributed(ws / 'roles' / 'captain' / 'b.md', 'workspace', 'Never push to main.'),
        )

    def test_directives_layer_workspace_before_project(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('lycia')
        project = _project(tmpdir, ws)
        # sorts first within its layer, but the workspace layer still leads
        tmpdir.write(project.dir / 'roles' / 'manager' / 'a.md', 'Watch the datacenter feeds.\n')
        tmpdir.write(ws / 'roles' / 'manager' / 'z.md', 'Keep goals moving.\n')
        compare(
            assemble(ws, project, 'manager', 'You are the manager.').text,
            expected='# Role: manager\n\nYou are the manager.\n\n'
            + _attributed(ws / 'roles' / 'manager' / 'z.md', 'workspace', 'Keep goals moving.')
            + '\n\n'
            + _attributed(
                project.dir / 'roles' / 'manager' / 'a.md', 'project', 'Watch the datacenter feeds.'
            ),
        )

    def test_a_subdir_is_structure_not_payload(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('lycia')
        tmpdir.write(ws / 'roles' / 'captain' / 'live.md', 'Live directive.\n')
        tmpdir.write(ws / 'roles' / 'captain' / 'drafts' / 'draft.md', 'Not yet.\n')
        rendered = assemble(ws, None, 'captain', 'intro')
        assert 'Not yet.' not in rendered.text
        assert 'Live directive.' in rendered.text

    def test_sources_record_every_searched_glob(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('lycia')
        project = _project(tmpdir, ws)
        directive = tmpdir.write(project.dir / 'roles' / 'agent' / 'a.md', 'Directive.\n')
        topic = tmpdir.write(ws / 'knowledge' / 'k.md', 'detail\n')
        compare(
            assemble(ws, project, 'agent', 'intro').sources,
            expected=(
                Source(str(ws / 'roles' / 'agent' / '*.md'), ()),
                Source(str(project.dir / 'roles' / 'agent' / '*.md'), (directive,)),
                Source(str(ws / 'principles' / '*.md'), ()),
                Source(str(project.dir / 'principles' / '*.md'), ()),
                Source(str(ws / 'knowledge' / '*.md'), (topic,)),
                Source(str(project.dir / 'knowledge' / '*.md'), ()),
            ),
        )

    def test_sources_skip_the_absent_project_axis(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('lycia')
        compare(
            assemble(ws, None, 'captain', 'intro').sources,
            expected=(
                Source(str(ws / 'roles' / 'captain' / '*.md'), ()),
                Source(str(ws / 'principles' / '*.md'), ()),
                Source(str(ws / 'knowledge' / '*.md'), ()),
            ),
        )


class TestMaterialize:
    def test_writes_content_addressed_file_and_logs(
        self, tmpdir: TempDir, full_logs: LogCapture
    ) -> None:
        ws = tmpdir.makedir('lycia')
        text = '# Principles\n\nVerify.'
        digest = sha256(text.encode()).hexdigest()
        source = Source(str(ws / 'principles' / '*.md'), (ws / 'principles' / 'verify.md',))
        path = materialize(ws, 'proj@g@agent', Rendered(text, (source,)))
        expected = ws / 'logs' / 'context' / f'proj@g@agent-{digest[:8]}.md'
        compare(path, expected=expected)
        compare(expected.read_text(), expected=text)
        full_logs.check(
            {
                'level': 'INFO',
                'session': 'proj@g@agent',
                'path': str(expected),
                'sha256': digest,
                'sources': {source.pattern: [str(source.matched[0])]},
                'message': 'context: rendered',
            }
        )

    def test_idempotent_for_identical_content(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('lycia')
        first = materialize(ws, 'n', Rendered('same text', ()))
        second = materialize(ws, 'n', Rendered('same text', ()))
        compare(second, expected=first)  # content-addressed: a re-run lands on the same artifact
        tmpdir.compare([first.name if first else '?'], path=ws / 'logs' / 'context')

    def test_sanitizes_a_url_bearing_name(self, tmpdir: TempDir) -> None:
        ws = tmpdir.makedir('lycia')
        path = materialize(ws, 'proj@pr-https://github.com/o/r/pull/1@agent', Rendered('text', ()))
        assert path is not None
        assert '/' not in path.name

    def test_none_when_nothing_to_inject(self, tmpdir: TempDir, full_logs: LogCapture) -> None:
        ws = tmpdir.makedir('lycia')
        assert materialize(ws, 'n', Rendered('', ())) is None
        assert not (ws / 'logs').exists()  # no file either
        full_logs.check()  # and no log line: nothing was rendered
