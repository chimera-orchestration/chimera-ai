from pathlib import Path

from loguru import logger

from chimera.commands.prompt import PACKAGED, Prompt, resolve


def init(prompts_dir: Path, name: str) -> tuple[Prompt, bool]:
    """Copy the packaged ``name`` template into ``prompts_dir``; return it and whether it was new.

    The starting point for a project's own version: an override wins whole, so editing a copy
    of the packaged text beats writing one from scratch against holes you have to remember.
    Idempotent, and never clobbers — an existing override is returned untouched, since it is
    exactly the work this would destroy.
    """
    if (existing := resolve(prompts_dir, name)).overridden:
        return existing, False
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (target := prompts_dir / f'{name}.md').write_text((PACKAGED / f'{name}.md').read_text())
    logger.bind(path=str(target), template=name).info('prompt init')
    return Prompt(name, target, overridden=True), True
