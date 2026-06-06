Working with your workspace
===========================

Chimera keeps everything it manages — projects, goals and agent worktrees —
under a single directory tree called the *workspace* (named ``lycia`` by
default). Create one with::

    ch init ~/lycia


Telling ``ch`` where the workspace is
-------------------------------------

Most commands need to know which workspace you mean. They find it in this order:

1. the ``CHIMERA_WORKSPACE`` environment variable, if set;
2. otherwise, by walking up from the current directory to the workspace root.

You will often run ``ch`` from a project checkout that lives *outside* the
workspace — for example a branch you have checked out to review. So set
``CHIMERA_WORKSPACE`` once in your shell profile and ``ch`` will work from
anywhere:

.. code-block:: zsh

    # ~/.zshrc
    export CHIMERA_WORKSPACE="$HOME/lycia"


Project, goal and actor
-----------------------

Within the workspace, ``ch`` infers the **project** from your current directory,
and — inside an agent worktree — the **goal** and **actor** too. From a project
checkout elsewhere, it identifies the project from the git repository itself, and
reads the goal and actor from the branch *only* when it is a ``<goal>/<actor>``
branch (a review or feature branch is never mistaken for a goal).

When ``ch`` cannot infer one of these, pass it explicitly:

``-p``, ``--project``
    the project name (under the workspace)

``-g``, ``--goal``
    the goal

``-a``, ``--actor``
    the actor; defaults to ``agent``
