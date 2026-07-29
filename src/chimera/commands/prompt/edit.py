import os
import shlex
import subprocess
from pathlib import Path

from loguru import logger

from chimera.commands.prompt import Prompt
from chimera.commands.prompt.init import init
from chimera.config import UserError


def edit(prompts_dir: Path, name: str, editor: str | None = None) -> Prompt:
    """Open the project's copy of template ``name`` in an editor, creating it first if absent.

    The interactive half of ``prompt init``: a human tuning a template edits a real file in
    the project, never the packaged original. ``editor`` defaults to ``$VISUAL`` then
    ``$EDITOR``; with neither set there is nothing to launch, which is a refusal rather than
    a guess (an editor that isn't yours can be unquittable).
    """
    prompt, _ = init(prompts_dir, name)
    command = editor or os.environ.get('VISUAL') or os.environ.get('EDITOR')
    if not command:
        raise UserError(
            f'no editor to open {prompt.source} with — set $VISUAL or $EDITOR, or pass --editor'
        )
    logger.bind(path=str(prompt.source), editor=command).info('prompt edit')
    if subprocess.run([*shlex.split(command), str(prompt.source)]).returncode:
        raise UserError(f'{command} exited non-zero; {prompt.source} may be unedited')
    return prompt
