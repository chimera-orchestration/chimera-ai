Installing
==========

You will need `uv <https://docs.astral.sh/uv/>`_, git, and the ``claude``
CLI — Claude Code is the agent harness Chimera launches by default.

Chimera installs from a checkout, as an *editable* tool: the ``ch`` on your
PATH runs straight out of a working tree you control, so the source of
whatever you are running is right there, and updating is a git operation.

.. every block on this page clones, installs or upgrades on the real machine —
.. none of it is run by the doc tests, which use this checkout itself.
.. skip: start

.. code-block:: console

    $ git clone https://github.com/chimera-orchestration/chimera-ai.git ~/vcs/git/chimera
    $ uv tool install --editable ~/vcs/git/chimera

That installs two identical entry points, ``ch`` and ``chimera`` — the docs use
the short one throughout. Check it answers:

.. code-block:: console

    $ ch --help

Then go and stand up your first goal: :doc:`tutorial`.

Working on Chimera itself
-------------------------

An editable install pins ``ch`` to one working tree, and whatever that tree has
checked out is the ``ch`` you run *everywhere* — so the moment you start editing
Chimera, every command on your machine is running your half-finished work.

Keep the two apart by installing from a second worktree, on a ``deploy`` branch
that only moves when you say so. Develop in the clone above; run from the
worktree:

.. code-block:: console

    $ git -C ~/vcs/git/chimera branch deploy main
    $ git -C ~/vcs/git/chimera worktree add ~/vcs/git/chimera-deploy deploy
    $ uv tool install --editable --force ~/vcs/git/chimera-deploy

``--force`` is what replaces the install you already have; without it ``uv``
leaves the existing one alone. The two working trees share one repository, so
the ``deploy`` branch is visible from both and costs nothing but a checkout.

Updating
--------

Two steps, and they do different things:

.. code-block:: console

    $ git -C ~/vcs/git/chimera pull --ff-only
    $ uv tool upgrade chimera-ai

The first moves the checkout ``ch`` runs from — and because the install is
editable, code changes are live the moment it lands. The second re-resolves the
tool's own environment against ``pyproject.toml``: dependencies are frozen when
you install, so a version that adds one leaves ``ch`` dying with
``ModuleNotFoundError`` on a module it now needs until you upgrade. Run both
rather than working out which kind of change you just pulled.

With the ``deploy`` worktree above, the first step is instead moving ``deploy``
onto the ``main`` you want to run — which is a check ``ch doctor`` already
owns:

.. code-block:: console

    $ git -C ~/vcs/git/chimera pull --ff-only
    $ ch doctor --fix -c chimera-up-to-date
    $ uv tool upgrade chimera-ai

That check finds the checkout ``ch`` is running from, fetches ``origin``, and
fast-forwards the ``deploy`` worktree onto ``main`` — refusing, and telling you
so, if the worktree is dirty or has diverged. It runs on every ``ch doctor``;
``-c`` is just there to leave the rest of the workspace alone while you
upgrade. See :doc:`health`.

.. skip: end
