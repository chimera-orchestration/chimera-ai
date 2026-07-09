"""The context injected into an agent session at launch.

Rendering follows the Principle/Knowledge split (see AGENTS.md core concepts):
principles are always-on and small, so they inline; knowledge is loaded on demand,
so it lands as an *index* of trigger lines — the agent reads a topic's file with its
own tools when the topic comes up. Nothing here ever touches the repo or worktree:
the render is written under the workspace's gitignored ``logs/`` and handed to the
harness by path, so it is both the injected input and the audit record of what was
injected (the log line binds the path and content hash).
"""

import re
from hashlib import sha256
from pathlib import Path

from loguru import logger

from chimera.context import Project, iter_projects

KNOWLEDGE_HINT = 'Before working on a topic listed below, read its file:'


def render(workspace: Path | None, project: Project | None) -> str:
    """The launch context for a scope: inlined principles, then the knowledge index.

    Workspace-level sources are joined by the pinned project's, each section appearing
    only when it has content. With no ``project`` the scope is the whole workspace, so
    every project's knowledge indexes, qualified by project name. Either axis may be
    absent (a project outside any workspace, a workspace-wide session): its sources
    simply drop out. Returns ``''`` when there is nothing to inject.
    """
    sections: list[str] = []
    principles = _contents(_dir(workspace, 'principles')) + _contents(
        _dir_of(project, 'principles')
    )
    if principles:
        sections.append('\n\n'.join(['# Principles', *principles]))
    if index := _knowledge_index(workspace, project):
        sections.append('\n'.join(['# Knowledge index', KNOWLEDGE_HINT, *index]))
    return '\n\n'.join(sections)


def role_context(workspace: Path, project: Project | None, role: str, intro: str) -> str:
    """The role section of a launch context: who the session is, then the role's directives.

    The caller owns the identity sentence — ``intro`` states affirmatively what the
    session *is* (never what it must not do). This function owns the ``# Role:`` header
    and the directives, inlined whole like principles (a role must know itself before
    anything else, so this section leads the render) and layered like them too: the
    workspace's ``roles/<role>/*.md`` first (the generic layer, reaching every project's
    instance of the role), then the pinned project's (its specific persona, so it can
    build on what the generic layer said). A scope with no project — the captain —
    has only the workspace layer; an absent dir on either level simply drops out, and
    no directives at all still introduces.
    """
    directives = _contents(workspace / 'roles' / role) + _contents(
        _dir_of(project, f'roles/{role}')
    )
    return '\n\n'.join([f'# Role: {role}', intro, *directives])


def materialize(workspace: Path, name: str, text: str) -> Path | None:
    """Write the rendered context for session ``name`` under the workspace's logs.

    The filename carries the content hash, so the artifact is immutable and a re-run
    with identical context lands on the same file. The log line binds the path and
    full sha256 — the recovery record of exactly what the session was launched with.
    ``None`` (and no file, no log) when there is nothing to inject.
    """
    if not text:
        return None
    digest = sha256(text.encode()).hexdigest()
    slug = re.sub(r'[^\w@.-]', '-', name)  # defensive: keep the filename filesystem-safe
    path = workspace / 'logs' / 'context' / f'{slug}-{digest[:8]}.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    logger.bind(session=name, path=str(path), sha256=digest).info('context: rendered')
    return path


def _dir(root: Path | None, sub: str) -> Path | None:
    return root / sub if root is not None else None


def _dir_of(project: Project | None, sub: str) -> Path | None:
    return project.dir / sub if project is not None else None


def _sources(directory: Path | None) -> list[Path]:
    """The markdown files under a source dir, stably ordered; [] when it doesn't exist."""
    if directory is None or not directory.is_dir():
        return []
    return sorted(directory.rglob('*.md'))


def _contents(directory: Path | None) -> list[str]:
    return [path.read_text().strip() for path in _sources(directory)]


def _knowledge_index(workspace: Path | None, project: Project | None) -> list[str]:
    """One trigger line per knowledge topic: ``- <topic>: <abs path>``.

    Workspace topics come bare; project topics are qualified by project name. A pinned
    project contributes only its own knowledge; an unpinned scope indexes every
    project's.
    """
    lines = [_line(path.stem, path) for path in _sources(_dir(workspace, 'knowledge'))]
    if project is not None:
        projects = [project]
    else:
        projects = iter_projects(workspace) if workspace is not None else []
    for p in projects:
        lines.extend(
            _line(f'{p.name}/{path.stem}', path) for path in _sources(_dir_of(p, 'knowledge'))
        )
    return lines


def _line(topic: str, path: Path) -> str:
    return f'- {topic}: {path.resolve()}'
