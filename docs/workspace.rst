The workspace
=============

Chimera keeps everything it manages — projects, goals and agent worktrees —
under a single directory tree called the :ref:`workspace <concept-workspace>`
(named ``lycia`` by convention). Create one with::

    ch init ~/lycia --captain pegasus

``--captain`` names the persona of the workspace's captain (see
:doc:`concepts`); it can be set later in ``config.yaml``.

Layout
------

.. code-block:: text

    ~/lycia/                    # a git repo of its own
      config.yaml               # kind: workspace  (+ captain, agent defaults)
      roles/                    # workspace-level role directives: roles/<role>/*.md
      principles/               # workspace-wide principles (inlined at every launch)
      knowledge/                # workspace-wide knowledge (indexed at launch)
      processes/                # reserved for agent runbooks (not built yet)
      logs/                     # chimera.jsonl and rendered launch contexts
      <project>/
        config.yaml             # kind: project + where its repo lives
        principles/  knowledge/  prompts/  roles/  processes/
        repo/                   # the project's repository (gitignored)
        worktrees/              # one worktree per goal: <goal>@agent (gitignored)

The workspace's own git tracks configuration and context — ``config.yaml``
files, ``principles/``, ``knowledge/``, ``prompts/``, ``roles/``,
``processes/`` — and ignores the live repositories and worktrees
(``*/repo/``, ``*/worktrees/``, ``logs/``). Your accumulated context is
versioned; the managed checkouts are not double-tracked.

``config.yaml``'s ``kind`` marker is the only on-disk signal of what a
directory is — depth and naming are never assumed.

Telling ``ch`` where the workspace is
-------------------------------------

.. envvar:: CHIMERA_WORKSPACE

   The absolute path of the workspace. When set, every ``ch`` command uses
   it; when unset, commands walk up from the current directory to the
   nearest ``kind: workspace`` marker.

You will often run ``ch`` from a checkout that lives *outside* the workspace
— a branch you have checked out to review, say — where the walk-up finds
nothing. So set :envvar:`CHIMERA_WORKSPACE` once in your shell profile and
``ch`` works from anywhere:

.. code-block:: zsh

    # ~/.zshrc
    export CHIMERA_WORKSPACE="$HOME/lycia"

``ch doctor`` checks this is in place and prints the exact line to add if
not (it never edits your shell profile itself).

Project, goal and actor
-----------------------

Within the workspace, ``ch`` infers the **project** from your current
directory, and — inside an agent worktree — the **goal** and **actor** too.
From a project checkout elsewhere, it identifies the project from the git
repository itself, and reads the goal and actor from the branch *only* when
it is a ``<goal>/<actor>`` branch of an existing goal — a review or feature
branch is never mistaken for a goal.

When ``ch`` cannot infer one of these, pass it explicitly:

``-p``, ``--project``
    the project name (under the workspace)

``-g``, ``--goal``
    the goal

``-a``, ``--actor``
    the actor; defaults to ``agent``

These flags are accepted at any position — ``ch -p demo goal ls``,
``ch goal -p demo ls`` and ``ch goal ls -p demo`` are equivalent; the most
specific (latest) position wins.

Listing commands are more forgiving than acting ones: a lister that cannot
pin a single project widens to all of them, while an action asks you to be
explicit. ``ch ls`` — the dashboard — deliberately never narrows by
directory: its job is the whole workspace, wherever you stand; focus it with
``-p``/``-g`` instead.

Configuration
-------------

``config.yaml`` at the workspace root, and per project, carries the small
amount of configuration Chimera has. Beyond the ``kind`` marker and the
captain's name, the main block is ``agent:`` — which harness and model
launched sessions use:

.. code-block:: yaml

    # config.yaml (workspace or project)
    agent:
      harness: claude   # optional; must be a registered harness
      model: opus       # optional; harness-native name

Each field resolves independently, nearest wins: a ``--harness``/``-m`` flag
beats the project's block, which beats the workspace's, which beats the
default (``claude``, on its own default model).
