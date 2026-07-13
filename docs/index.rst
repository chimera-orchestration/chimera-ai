.. include:: ../README.rst

What is this?
=============

Chimera manages a *workspace*: a git-tracked directory tree holding the
projects you care about. For each piece of work — a *goal* — it stands up an
isolated git branch and worktree, launches an AI coding agent there, and gives
you the commands to direct that agent, review what it produced, and land the
result. You talk to a workspace-level *captain* (or a per-project *manager*)
in plain chat; agents do the work; every action is logged with enough detail
to audit or undo it.

If you want AI agents working on several things at once, in several
repositories, without them treading on each other or on you — that is what
``ch`` is for.

Start with the :doc:`tutorial`. When you want to understand the moving parts,
read :doc:`concepts`. Day to day, the guides below cover each workflow, and
``ch help`` is the always-current command inventory (see :doc:`reference`).

.. toctree::
   :maxdepth: 1
   :caption: Getting started

   tutorial

.. toctree::
   :maxdepth: 1
   :caption: Understanding Chimera

   concepts
   workspace

.. toctree::
   :maxdepth: 1
   :caption: Guides

   directing
   landing
   collaboration
   health
   logging
   completion

.. toctree::
   :maxdepth: 1
   :caption: Reference

   reference
   changes
   license
