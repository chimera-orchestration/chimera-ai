Concepts
========

Chimera has a small, deliberate vocabulary. The names below are used
consistently across the CLI, its output, its logs and these docs — learning
them once pays for itself quickly.

.. _concept-workspace:

Workspace
---------

The directory tree Chimera manages; everything happens inside it. It is
itself a git repository, tracking your configuration, principles and
knowledge — but *not* the repositories it manages, which are gitignored
within it. One workspace usually serves one human. The conventional name is
``lycia``. :doc:`workspace` covers its layout and how commands find it.

.. _concept-project:

Project
-------

One repository under management, living in its own directory under the
workspace. Three flavours, differing only in where the repository is:

* **workspace-only** — created by ``ch project new``: a fresh local
  repository with no remote. Graduate it later with ``ch project push
  <url>``, after which nothing distinguishes it from the next flavour.
* **remote-backed** — ``ch project add <git-url>`` clones the repository
  into the project directory.
* **registered** — ``ch project add <path>`` tracks a checkout you already
  have elsewhere on disk; the repository stays where it is.

A *reference* project is a fourth, degenerate case: no repository at all,
just extracted knowledge — useful for tracking what you know about a codebase
you don't work on.

.. _concept-goal:

Goal, actor, branch, worktree
-----------------------------

A **goal** is a thing that needs doing — "add-greeting",
"fix-login-timeout". Each participant in a goal is an **actor**: you (the
``human``) or an ``agent``. The naming scheme is rigid and worth memorising:

* each actor works on **branch** ``<goal>/<actor>`` — e.g.
  ``add-greeting/agent``, ``add-greeting/human``;
* an agent additionally gets a **worktree** — an isolated checkout at
  ``<project>/worktrees/<goal>@<actor>`` — because an agent needs somewhere
  of its own to work. You check your branch out wherever you like.

The separators are load-bearing: branches use ``/`` (git's natural
namespacing), worktree directories use ``@`` (``/`` would nest directories; a
dash would blur into kebab-case goal names; ``@`` can't appear in a goal or
actor, so the name always splits cleanly).

Only the agent's branch and worktree are created up front. Your ``human``
branch is materialised on demand by ``ch goal sync`` — a short-lived
experiment never accrues a dead branch.

Roles: captain, manager, agent
------------------------------

Every Chimera-launched AI session has a **role** — what it was launched *as*:

* the **captain** — the workspace-level session you chat with to direct work
  across all projects. It has no goal, branch or worktree; it works on the
  workspace as a whole. Its persona name is yours to choose
  (``ch init --captain pegasus``) and is how you'll think of it.
* a **manager** — the same kind of conversation, scoped to one project: its
  goals and agents are the manager's to run.
* an **agent** — the worker on one goal, confined to that goal's worktree
  and branch.

Roles are fenced as well as named: a manager's session physically lacks the
commands and flags that would reach outside its project, an agent's those
that would reach outside its goal. Each role can also be given standing
directives — markdown files in ``roles/<role>/`` at the workspace level
(applying to every project) and per project (that project only).

Principles and knowledge
------------------------

Two kinds of context, split by *when it's needed*:

* a **principle** is context an agent must always have — small, always-on.
  Files in ``principles/`` (workspace-wide) and ``<project>/principles/``
  are inlined whole into every launched session.
* **knowledge** is context loaded on demand — larger, topic-shaped. Files in
  ``knowledge/`` directories are *indexed* into the session (one trigger
  line per file); the agent reads a file with its own tools when the topic
  comes up.

A principle big enough that you'd want it loaded lazily is knowledge
misfiled: move it.

At every launch, Chimera assembles role directives, principles and the
knowledge index into a single rendered context file, hands it to the harness,
and logs exactly what went in — so you can always audit what a session was
told. Every launching command's ``--dry`` shows this render, sources and all.

.. _concept-errand:

Errand
------

A one-shot, read-only agent dispatched into another project to answer a
question: ``ch errand <project> "<question>"`` prints its report and cleans
up after itself. Cross-project *reading* is deliberately cheap; cross-project
*writing* is not a thing any single session can do. See
:ref:`errands <guide-errands>`.

Reserved vocabulary
-------------------

Three more terms are defined by design but **not yet built** — you will meet
them in design discussion, not in the CLI:

* a **Task** — a tracked unit of work, discovered while planning or
  executing a goal;
* a **Process** — a recurring job's runbook, one markdown file an agent can
  run from alone (distinct from an operating-system process);
* a **Service** — a long-running system process managed by Chimera.

Nothing in today's ``ch help`` operates on them.
