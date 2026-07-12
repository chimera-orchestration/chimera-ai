"""The context injected into an agent session at launch.

Rendering follows the Principle/Knowledge split (see AGENTS.md core concepts):
principles are always-on and small, so they inline; knowledge is loaded on demand,
so it lands as an *index* of trigger lines — the agent reads a topic's file with its
own tools when the topic comes up. Every inlined file is source-attributed: a comment
line names the file it came from and its layer, so the session can resolve a tension
between directives by layer order (project builds on workspace), cite a directive
back to its file, and propose an edit to the right place. Nothing here ever touches
the repo or worktree: the render is written under the workspace's gitignored
``state/`` and handed to the harness by path, so it is both the injected input and
the audit record of what was injected (the log line binds the path, content hash and
the sources searched).
"""

import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from loguru import logger

from chimera.context import Project, iter_projects

KNOWLEDGE_HINT = 'Before working on a topic listed below, read its file:'

WORKSPACE_LAYER = 'workspace'
PROJECT_LAYER = 'project'


@dataclass(frozen=True)
class Source:
    """One glob a render searched and the files it matched — none is a finding, not a gap.

    An empty ``matched`` is exactly the "why isn't my directive appearing" case, so
    sources are recorded for every searched dir, existing or not.
    """

    pattern: str
    matched: tuple[Path, ...]


@dataclass(frozen=True)
class Rendered:
    """A launch context: the text to inject and the sources it was assembled from."""

    text: str
    sources: tuple[Source, ...]


def assemble(workspace: Path, project: Project | None, role: str, intro: str) -> Rendered:
    """The launch context for a scope: role section, principles, then the knowledge index.

    The ``# Role:`` section leads (a session must know itself before anything else):
    the caller owns ``intro`` — an affirmative statement of what the session *is*
    (never what it must not do) — followed by the role's directives. Directives and
    principles inline whole, each file behind a source-attribution line, and layer
    the same way: the workspace's first (the generic layer, reaching every project's
    instance of the role), then the pinned project's (its specific persona, building
    on what the generic layer said). A scope with no project — the captain — has only
    the workspace layer; an absent dir on either level simply drops out, and no
    directives at all still introduces. Knowledge is never inlined: a pinned project
    indexes only its own topics, an unpinned scope every project's, qualified by name.
    """
    sources: list[Source] = []
    directives = _attributed(workspace / 'roles' / role, WORKSPACE_LAYER, sources) + _attributed(
        _dir_of(project, f'roles/{role}'), PROJECT_LAYER, sources
    )
    sections = ['\n\n'.join([f'# Role: {role}', intro, *directives])]
    principles = _attributed(workspace / 'principles', WORKSPACE_LAYER, sources) + _attributed(
        _dir_of(project, 'principles'), PROJECT_LAYER, sources
    )
    if principles:
        sections.append('\n\n'.join(['# Principles', *principles]))
    if index := _knowledge_index(workspace, project, sources):
        sections.append('\n'.join(['# Knowledge index', KNOWLEDGE_HINT, *index]))
    return Rendered('\n\n'.join(sections), tuple(sources))


def materialize(workspace: Path, name: str, rendered: Rendered) -> Path | None:
    """Write the rendered context for session ``name`` under the workspace's logs.

    The filename carries the content hash, so the artifact is immutable and a re-run
    with identical context lands on the same file. The log line binds the path, the
    full sha256 and the sources map (each glob searched → the files it matched) — the
    recovery record of exactly what a session was launched with, and why a directive
    did or didn't make it in. ``None`` (and no file, no log) when there is nothing
    to inject.
    """
    if not rendered.text:
        return None
    digest = sha256(rendered.text.encode()).hexdigest()
    slug = re.sub(r'[^\w@.-]', '-', name)  # defensive: keep the filename filesystem-safe
    path = workspace / 'state' / 'context' / f'{slug}-{digest[:8]}.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered.text)
    logger.bind(
        session=name,
        path=str(path),
        sha256=digest,
        sources={s.pattern: [str(p) for p in s.matched] for s in rendered.sources},
    ).info('context: rendered')
    return path


def _dir_of(project: Project | None, sub: str) -> Path | None:
    return project.dir / sub if project is not None else None


def _sources(directory: Path) -> list[Path]:
    """The markdown files directly in a source dir, stably ordered; [] when it doesn't exist.

    Deliberately non-recursive: a subdir (``drafts/``, an archive) is structure, not
    payload — only files sitting at the top level are live context.
    """
    if not directory.is_dir():
        return []
    return sorted(directory.glob('*.md'))


def _record(directory: Path | None, sources: list[Source]) -> list[Path]:
    """Search a dir, recording the glob and its matches; a ``None`` dir is an absent axis."""
    if directory is None:
        return []
    matched = _sources(directory)
    sources.append(Source(str(directory / '*.md'), tuple(matched)))
    return matched


def _attributed(directory: Path | None, layer: str, sources: list[Source]) -> list[str]:
    """Each file's content behind a line naming where it came from and which layer it is."""
    return [
        f'<!-- {path.resolve()} ({layer}) -->\n{path.read_text().strip()}'
        for path in _record(directory, sources)
    ]


def _knowledge_index(workspace: Path, project: Project | None, sources: list[Source]) -> list[str]:
    """One trigger line per knowledge topic: ``- <topic>: <abs path>``.

    Workspace topics come bare; project topics are qualified by project name. A pinned
    project contributes only its own knowledge; an unpinned scope indexes every
    project's.
    """
    lines = [_line(path.stem, path) for path in _record(workspace / 'knowledge', sources)]
    for p in [project] if project is not None else iter_projects(workspace):
        lines.extend(
            _line(f'{p.name}/{path.stem}', path) for path in _record(p.dir / 'knowledge', sources)
        )
    return lines


def _line(topic: str, path: Path) -> str:
    return f'- {topic}: {path.resolve()}'
