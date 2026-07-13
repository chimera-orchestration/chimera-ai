"""Installing chimera's hooks into the user's global Claude settings.

User-wide (``~/.claude/settings.json``) so they fire for *every* session — SessionStart/End
feed the archive, UserPromptSubmit drains mail into the turn. The merge is idempotent and
preserves anything else in the file (the user's own hooks and settings). Doctor drives this
so nobody has to remember ``ch hook install`` (there is no such command — doctor is it).
"""

import json
from pathlib import Path
from typing import Any

# event → the ``ch`` command chimera wires onto it.
CHIMERA_HOOKS = {
    'SessionStart': 'ch hook session-start',
    'SessionEnd': 'ch hook session-end',
    'UserPromptSubmit': 'ch msg drain --inject',
}


def settings_path() -> Path:
    """The user's global Claude settings file (``~/.claude/settings.json``)."""
    return Path.home() / '.claude' / 'settings.json'


def read(path: Path) -> dict[str, Any]:
    """Parse ``path`` as JSON, or ``{}`` when it is absent."""
    return json.loads(path.read_text()) if path.exists() else {}


def write(path: Path, settings: dict[str, Any]) -> None:
    """Write ``settings`` to ``path`` as pretty JSON, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + '\n')


def missing_hooks(settings: dict[str, Any]) -> list[str]:
    """The chimera hook events not yet wired into ``settings``, in install order."""
    return [
        event
        for event, command in CHIMERA_HOOKS.items()
        if not _installed(settings, event, command)
    ]


def merge(settings: dict[str, Any]) -> dict[str, Any]:
    """``settings`` with chimera's hooks merged in — idempotent, preserving everything else."""
    hooks = settings.setdefault('hooks', {})
    for event, command in CHIMERA_HOOKS.items():
        if not _installed(settings, event, command):
            hooks.setdefault(event, []).append({'hooks': [{'type': 'command', 'command': command}]})
    return settings


def _installed(settings: dict[str, Any], event: str, command: str) -> bool:
    entries = settings.get('hooks', {}).get(event, [])
    return any(h.get('command') == command for entry in entries for h in entry.get('hooks', []))
