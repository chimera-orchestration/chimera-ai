"""The prompt templates chimera renders for you: ``review`` and ``pr``.

Each ships with chimera and is overridable per project by a file of the same name in
``<project>/prompts/``. An override wins *whole* — nothing is merged — so the packaged
text is the starting point (``ch prompt init``) rather than a base to extend.

The set of names is derived from the packaged directory itself, so a template added
there is listed, completed and copyable without a second list to keep in step. What each
template's ``$`` holes fill with is declared here too (:data:`HOLES`) — the one place a
default like :data:`REVIEW_STEP` is written down, so ``ch prompt show`` can print it
instead of leaving it buried in the renderer. A test pins each declaration against the
identifiers the packaged template actually uses, so the two can't drift.
"""

from dataclasses import dataclass
from pathlib import Path

from chimera.config import UserError

# The packaged templates live beside the code (chimera/prompts/), as ch init's workspace
# template does — a real directory, so `prompt init` can copy one out.
PACKAGED = Path(__file__).parents[2] / 'prompts'

# What `$REVIEW` renders as unless `ch review --review` says otherwise: the one step whose
# *how* is a per-PR judgement call (which review command, or none at all), while the
# template keeps the surrounding orientation and write-up instructions.
REVIEW_STEP = "Run `/review` to gather the PR's diff and produce findings."


@dataclass(frozen=True)
class Hole:
    """One ``$NAME`` a template renders, and what fills it."""

    name: str
    fills: str  # what the value is, for a hole only the launch can fill
    default: str | None = None  # the literal text used unless overridden
    flag: str | None = None  # the CLI option that overrides it, if any

    @property
    def value(self) -> str:
        """What this renders as: the default text, else a placeholder naming the source."""
        return self.default if self.default is not None else f'<{self.fills}>'


HOLES: dict[str, tuple[Hole, ...]] = {
    'pr': (
        Hole('PROJECT', 'the project name'),
        Hole('GOAL', 'the goal being published'),
        Hole('BASE', 'the branch the PR targets'),
        Hole('SOURCE', "the actor branch carrying the goal's work"),
        Hole('COMMITS', "the branch's full commit messages, oldest first"),
    ),
    'review': (
        Hole('PR', 'the pull request number'),
        Hole('PR_URL', "the pull request's url"),
        Hole('PR_TITLE', "the pull request's title"),
        Hole('BASE', 'the branch the PR targets'),
        Hole('GOAL', 'the goal the review runs as'),
        Hole('PROJECT', 'the project name'),
        Hole('REVIEW', 'how to gather the diff', default=REVIEW_STEP, flag='--review'),
    ),
}


def names() -> list[str]:
    """The template names, sorted — derived from the packaged files themselves."""
    return sorted(path.stem for path in PACKAGED.glob('*.md'))


@dataclass(frozen=True)
class Prompt:
    """A template as it currently resolves for one project."""

    name: str
    source: Path
    overridden: bool  # True when source is the project's copy, not the packaged default

    @property
    def text(self) -> str:
        return self.source.read_text()

    @property
    def holes(self) -> tuple[Hole, ...]:
        return HOLES[self.name]


def resolve(prompts_dir: Path, name: str) -> Prompt:
    """The template ``name`` resolves to for ``prompts_dir``: its override, else the packaged.

    The single place that knows the cascade — ``ch review`` and ``ch goal pr`` render what
    this returns, so ``ch prompt show`` can never disagree with what a launch actually used.
    """
    if name not in names():
        raise UserError(f'no prompt template named {name} — there is {", ".join(names())}')
    override = prompts_dir / f'{name}.md'
    if override.is_file():
        return Prompt(name, override, overridden=True)
    return Prompt(name, PACKAGED / f'{name}.md', overridden=False)
