from chimera.context import Scope, iter_projects
from chimera.worktrees import goals


def goals_in_scope(scope: Scope) -> list[tuple[str, str]]:
    """(project, goal) pairs in scope, sorted: one project when pinned, else every project."""
    projects = [scope.project] if scope.project is not None else iter_projects(scope.workspace)
    return sorted((p.name, goal) for p in projects for goal in goals(p.worktrees))
