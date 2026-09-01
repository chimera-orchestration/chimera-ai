import ast
import importlib
import re
from pathlib import Path

from testfixtures import compare

ROOT = Path(__file__).parent.parent / 'src'

ROLE = re.compile(r':(?:class|meth|func|data|attr|exc|mod):`([^`]+)`')
"""The reST cross-reference roles the docstrings use."""


def _module(path: Path) -> str:
    return '.'.join(path.relative_to(ROOT).with_suffix('').parts).removesuffix('.__init__')


def _targets(path: Path) -> list[tuple[str, str, str | None]]:
    """Every role target in ``path`` as ``(target, module, enclosing class)``.

    Whitespace is stripped rather than collapsed: a docstring wraps where it likes, and
    the line break often falls inside the dotted path itself.
    """
    home = _module(path)
    found: list[tuple[str, str, str | None]] = []

    def collect(doc: str | None, cls: str | None) -> None:
        found.extend((''.join(raw.split()), home, cls) for raw in ROLE.findall(doc or ''))

    def walk(node: ast.AST, cls: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            match child:
                case ast.ClassDef():  # a class docstring's bare names mean its own members
                    collect(ast.get_docstring(child), child.name)
                    walk(child, child.name)
                case ast.FunctionDef() | ast.AsyncFunctionDef():
                    collect(ast.get_docstring(child), cls)
                    walk(child, cls)
                case _:
                    walk(child, cls)

    collect(ast.get_docstring(ast.parse(path.read_text())), None)
    walk(ast.parse(path.read_text()), None)
    return found


def _resolves(target: str, home: str, cls: str | None) -> bool:
    """Whether ``target`` names something that exists, the way a reader would read it.

    Three ways, in the order Sphinx would try them: an absolute dotted path through any
    importable module (ours or the stdlib's), then the enclosing class, then the module
    the docstring lives in.
    """
    parts = target.lstrip('~').lstrip('.').split('.')
    for cut in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module('.'.join(parts[:cut]))
        except ImportError:
            continue
        try:
            for attribute in parts[cut:]:
                obj = getattr(obj, attribute)
            return True
        except AttributeError:
            break
    module = importlib.import_module(home)
    owner = [getattr(module, cls)] if cls and hasattr(module, cls) else []
    for base in [*owner, module]:
        obj = base
        try:
            for attribute in parts:
                obj = getattr(obj, attribute)
            return True
        except AttributeError:
            continue
    return False


def test_every_docstring_reference_resolves() -> None:
    # `sphinx.ext.autodoc` is enabled but no document uses it, so nothing renders these
    # docstrings and nothing has ever resolved a role in one — they read as links and are
    # decorative. This is what makes them true: it found `chimera.archive.Session` still
    # named after the rename to ArchiveSession, and `_source` after it became source_branch.
    broken = [
        (str(path.relative_to(ROOT)), target)
        for path in sorted(ROOT.rglob('*.py'))
        for target, home, cls in _targets(path)
        if not _resolves(target, home, cls)
    ]
    compare(broken, expected=[])


def test_the_check_would_notice_a_ghost() -> None:
    # the pin only means something if it fails on a name that isn't there
    assert _resolves('chimera.archive.ArchiveSession', 'chimera.agents', None)
    assert not _resolves('chimera.archive.Session', 'chimera.agents', None)
    assert _resolves('live', 'chimera.agents', 'Agent')  # class-relative
    assert not _resolves('live', 'chimera.agents', None)  # …and only within its class
    assert _resolves('string.Template', 'chimera.commands.review', None)  # stdlib too
