from collections import Counter
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


@dataclass
class Exclusions:
    """The ``-x/--exclude`` tokens for a doctor run, tracking what they matched.

    A token matches a finding when it equals the check's name or appears in the
    finding's message — so ``-x worktree-branch`` mutes a whole check and
    ``-x datasets-partitioned@agent`` mutes one worktree's findings. Checks call
    :meth:`matches` (pure) before *applying* a fix, always with the message the
    plain report shows — the text the user copied the token from — never a
    post-fix variant; the doctor driver calls :meth:`drop` (recording) to filter
    what's reported, so each excluded finding is counted exactly once.
    """

    tokens: tuple[str, ...] = ()
    hits: Counter[str] = field(default_factory=Counter)  # token → findings it excluded

    def matches(self, check: str, message: str) -> bool:
        return any(token == check or token in message for token in self.tokens)

    def drop(self, finding: Finding) -> bool:
        matched = [t for t in self.tokens if t == finding.check or t in finding.message]
        for token in matched:
            self.hits[token] += 1
        return bool(matched)

    @property
    def excluded(self) -> int:
        """How many findings the tokens excluded."""
        return sum(self.hits.values())

    @property
    def unmatched(self) -> tuple[str, ...]:
        """Tokens that excluded nothing — likely typos, surfaced as warnings."""
        return tuple(token for token in self.tokens if not self.hits[token])


class Check(Protocol):
    """A self-contained workspace check; add/retire by editing the CHECKS registry."""

    name: str

    def run(self, workspace: Path, fix: bool, exclude: Exclusions) -> Iterable[Finding]: ...


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
