Shell completion
================

``ch`` completes its commands, options and their values for zsh and bash:
project names (``-p``, ``ch project rm``), existing goals (``-g``, ``ch goal
finish``, ``ch worktree rm``) and actors (``-a``, ``ch worktree add``). Values
are read live from the workspace, scoped the same way as the ``ls`` commands:
narrowed by flags already on the line or by the directory you're standing in,
widened to the whole workspace otherwise.

There are two ways to enable it.

Let ``ch`` install it
---------------------

Run once::

    ch --install-completion

This detects your shell, writes the completion hook into your shell's startup
files, and tells you what it changed. Start a new shell to pick it up. Use
``ch --show-completion`` to inspect the script without installing anything.

Add it to your shell startup yourself
-------------------------------------

If you prefer to keep your dotfiles hand-managed, add the matching line
yourself.

zsh — add to ``~/.zshrc`` (after ``compinit`` is loaded)::

    eval "$(env _CH_COMPLETE=source_zsh ch)"

bash — add to ``~/.bashrc``::

    eval "$(env _CH_COMPLETE=source_bash ch)"

The script is a thin shim that calls back into ``ch`` at completion time, so
it never goes stale as commands evolve.

Completion is registered per command name: the ``chimera`` spelling needs its
own line, with ``_CHIMERA_COMPLETE`` in place of ``_CH_COMPLETE`` and
``chimera`` in place of ``ch``.
