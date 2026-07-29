from pathlib import Path

from chimera.commands.prompt import Prompt, names, resolve


def prompts(prompts_dir: Path) -> list[Prompt]:
    """Every template, in name order, resolved as it stands for ``prompts_dir``."""
    return [resolve(prompts_dir, name) for name in names()]
