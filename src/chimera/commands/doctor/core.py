from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml


@dataclass(frozen=True)
class Finding:
    """A single thing a check noticed about the workspace."""

    check: str  # the reporting check's name, for grouping in output
    message: str
    resolved: bool = field(kw_only=True)  # was it actually fixed on this run?
    fixable: bool = field(kw_only=True)  # could --fix handle it at all?


class Check(Protocol):
    """A self-contained workspace check; add/retire by editing the CHECKS registry."""

    name: str

    def run(self, workspace: Path, fix: bool) -> Iterable[Finding]: ...


def read_raw(directory: Path) -> dict[str, Any] | None:
    """The directory's config.yaml as a raw dict, {} if empty, None if absent.

    Deliberately bypasses chimera.config: doctor must read *legacy*, pre-schema
    configs that wouldn't validate.
    """
    path = directory / 'config.yaml'
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text())
    return data if isinstance(data, dict) else {}


def write_config(directory: Path, data: dict[str, Any]) -> None:
    """Write directory's config.yaml, preserving key order (so kind: leads)."""
    (directory / 'config.yaml').write_text(yaml.safe_dump(data, sort_keys=False))


def iter_project_dirs(workspace: Path) -> Iterator[Path]:
    """Immediate child dirs of the workspace that hold a config.yaml, sorted."""
    for child in sorted(workspace.iterdir()):
        if child.is_dir() and (child / 'config.yaml').exists():
            yield child


def project_repo(project_dir: Path) -> Path | None:
    """The repo path recorded in a project's config.yaml, or None if unset."""
    raw = read_raw(project_dir) or {}
    repo = raw.get('repo')
    return Path(repo) if repo else None
