Command reference
=================

The command reference is the CLI itself. It is derived from the live command
tree on every invocation — never a hand-maintained list — so it cannot drift
from what your installed version actually does. These docs deliberately
don't duplicate it.

``ch help``
    every command with its one-line summary, one flat screen. ``-v`` adds
    each command's options and synonyms; ``--json`` emits the same index as
    structured data.

``ch <command> --help``
    the full detail for one command or group: arguments, options, defaults.

``ch prime``
    the editorial counterpart: not what exists, but how to work *here, right
    now* — the golden-path loop for wherever you're standing (workspace,
    project, or goal worktree). It's the same orientation Chimera injects
    into every session it launches.

Two things worth knowing when reading help output:

* **Synonyms don't show.** Some commands accept alternate spellings
  (``ch goal new`` for ``goal start``, ``ls`` and ``list`` everywhere) —
  they tab-complete, but ``--help`` and the logs only ever show the
  canonical name. ``ch help -v`` lists them.

* **AI sessions see a smaller tree.** Chimera strips human-only capability
  from any session driven by an AI agent: ``--force`` and ``--dangerous``
  are physically absent, ``logtail`` is missing, and a role-scoped session
  (a manager, a goal's agent) loses every command outside its scope. The
  flags aren't hidden or merely forbidden — the session's parser has never
  heard of them. So if an agent reports that a flag "doesn't exist", it may
  simply be one reserved for you: check from your own terminal, where
  ``ch help`` always shows the full tree.
