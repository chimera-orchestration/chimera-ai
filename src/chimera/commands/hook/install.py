"""Installing chimera's hooks into the user's global Claude settings.

User-wide (``~/.claude/settings.json``) so they fire for *every* session — SessionStart/End
feed the archive, UserPromptSubmit delivers mail into the turn. The merge is idempotent and
preserves anything else in the file (the user's own hooks and settings), while sweeping any
superseded chimera hook spelling (:data:`STALE_HOOKS`) so an upgraded ``ch`` doesn't leave
the old wiring firing beside the new. Doctor drives this so nobody has to remember
``ch hook install`` (there is no such command — doctor is it).
"""

import json
from pathlib import Path
from typing import Any

# event → the ``ch`` command chimera wires onto it.
CHIMERA_HOOKS = {
    'SessionStart': 'ch hook session-start',
    'SessionEnd': 'ch hook session-end',
    'UserPromptSubmit': 'ch hook deliver',
}

# Superseded spellings merge() sweeps out. The drain-based injection surfaced only the
# messages it claimed itself, so any other `ch msg drain` silenced the hook forever —
# the delivery trap `ch hook deliver` closes.
STALE_HOOKS = frozenset({'ch msg drain --inject'})


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


def stale_hooks(settings: dict[str, Any]) -> list[str]:
    """The superseded chimera hook commands still wired into ``settings``, sorted."""
    return sorted(
        {
            hook['command']
            for entries in settings.get('hooks', {}).values()
            for entry in entries
            for hook in entry.get('hooks', [])
            if hook.get('command') in STALE_HOOKS
        }
    )


def merge(settings: dict[str, Any]) -> dict[str, Any]:
    """``settings`` with chimera's hooks merged in and superseded ones swept — idempotent,
    preserving everything else."""
    hooks = settings.setdefault('hooks', {})
    for event in list(hooks):
        entries = [entry for entry in hooks[event] if _sweep(entry)]
        if entries:
            hooks[event] = entries
        else:
            del hooks[event]
    for event, command in CHIMERA_HOOKS.items():
        if not _installed(settings, event, command):
            hooks.setdefault(event, []).append({'hooks': [{'type': 'command', 'command': command}]})
    return settings


def _sweep(entry: dict[str, Any]) -> bool:
    """Drop stale hooks from ``entry`` in place; False when that emptied it entirely."""
    if 'hooks' not in entry:
        return True  # not command-shaped — not ours, keep verbatim
    entry['hooks'] = [h for h in entry['hooks'] if h.get('command') not in STALE_HOOKS]
    return bool(entry['hooks'])


def _installed(settings: dict[str, Any], event: str, command: str) -> bool:
    entries = settings.get('hooks', {}).get(event, [])
    return any(h.get('command') == command for entry in entries for h in entry.get('hooks', []))
