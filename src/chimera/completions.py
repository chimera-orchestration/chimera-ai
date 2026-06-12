"""Tab-completion callbacks for value-taking parameters.

Completion runs in-process on every TAB (Click calls back with ``_CH_COMPLETE`` set),
so these reuse the listers and scope like them: narrowed by flags/cwd, widened to the
workspace otherwise. A completer must never raise or print — a failure (no workspace,
ghost project, half-typed junk) silently completes to nothing.

Group callbacks are not invoked while completing (Click only resilient-parses the
line), so a ``-p`` typed at any level is read from the raw context chain rather than
the usual ``Overrides`` on ``ctx.obj``.
"""

from pathlib import Path

from typer._click.core import Context

from chimera.commands.goal.ls import goals_in_scope
from chimera.commands.project.ls import projects
from chimera.context import resolve_scope, resolve_workspace
from chimera.worktrees import ACTORS


def _typed_project(ctx: Context) -> str | None:
    """The most specific ``-p``/``--project`` already typed on the line, if any."""
    current: Context | None = ctx
    while current is not None:
        if (project := current.params.get('project')) is not None:
            return str(project)
        current = current.parent
    return None


def complete_project(incomplete: str) -> list[str]:
    """Tracked project names matching the typed prefix."""
    try:
        return [n for n in projects(resolve_workspace(Path.cwd())) if n.startswith(incomplete)]
    except Exception:
        return []


def complete_goal(ctx: Context, incomplete: str) -> list[str]:
    """Existing goal names matching the typed prefix, scoped like ``goal ls``.

    A ``-p`` anywhere on the line (or cwd inference) pins one project; otherwise
    every project's goals are offered, bare and deduplicated.
    """
    try:
        scope = resolve_scope(Path.cwd(), project=_typed_project(ctx))
        return sorted({g for _, g in goals_in_scope(scope) if g.startswith(incomplete)})
    except Exception:
        return []


def complete_actor(incomplete: str) -> list[str]:
    """Actor names matching the typed prefix."""
    return [actor for actor in ACTORS if actor.startswith(incomplete)]
