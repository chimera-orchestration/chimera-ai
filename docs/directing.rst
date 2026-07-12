Directing work
==============

This guide covers getting work *started* and *supervised*: surveying the
workspace, chatting with the captain or a manager, starting goals, and
managing agent sessions. Landing the results is :doc:`landing`.

Seeing what's happening
-----------------------

``ch ls`` is the dashboard: every project, its goals, and any agents running
under them, wherever you run it from. The scoped listers — ``ch project
ls``, ``ch goal ls``, ``ch agent ls`` — each enumerate one axis, narrowed by
where you stand (or ``-p``/``-g``) and widened to the whole workspace when
nothing pins them. ``ch agent ls`` heads its output with a ``scope:`` banner
naming what it's bounded to, and hides stale session records unless you ask
with ``-v``.

Chat: the captain and managers
------------------------------

``ch chat`` launches a conversation at the current scope:

* at the workspace root, the **captain** — the workspace-level agent that
  directs all work. It knows the ``ch`` command set and works across every
  project: tell it what you want done and it starts goals, checks agents and
  lands results on your behalf.
* inside a project directory (or with ``-p``), that project's **manager** —
  the same conversation scoped to one project's goals and agents.

A chat deliberately sits *alongside* whatever else is running in the same
place, but a scope only gets one live chat at a time — starting a second
points you at the first instead. ``--resume``/``-r`` revives the scope's
previous, exited chat with its history intact.

There is no goal-level chat: a goal already has its agent. Asking for one
(running ``ch chat`` inside a goal worktree, or passing ``-g``) refuses,
pointing you at ``ch agent resume -g <goal>`` to talk to the agent itself.

Starting goals
--------------

``ch goal start <goal> [prompt]`` does the whole setup in one command: a
branch ``<goal>/agent``, a worktree ``worktrees/<goal>@agent``, and an agent
launched there with the project's assembled context (see :doc:`concepts`).
Branches start from the freshest of local and remote default branches —
never from whatever happens to be checked out — or from ``--from <ref>``.

* **without a prompt** — the session opens interactively in your terminal;
  you drive it directly.
* **with a prompt** — the agent launches in the background and works
  autonomously; attach later with ``ch agent resume``.

``ch goal adopt <branch> [prompt]`` is the same launcher for work that
already exists: it restructures an existing branch into the goal shape (the
branch becomes ``<branch>/human``, the agent's branch is split off its tip),
creates the worktree, and launches the agent — the way to hand an in-flight
feature branch to an agent. It is idempotent: re-running just relaunches.

``ch worktree add --goal <goal>`` is the setup half alone — branches and
worktree, no launch — for when you want the structure without an agent yet.

Managing agent sessions
-----------------------

Each goal's agent is a session Chimera can find again by name:

* ``ch agent start -g <goal>`` — launch an agent in an existing goal
  worktree.
* ``ch agent resume -g <goal>`` — revive the goal's exited session, history
  intact. Chimera knows the worktree, so this works from anywhere — just
  name the project with ``-p`` when you're not standing in it.
* ``ch agent stop -g <goal>`` — stop the live session cleanly (a polite
  signal and a wait — never a hard kill; a session that won't die is
  reported for you to inspect, and a goal with no worktree is an error, so a
  typo never reads as "nothing running").

While a session is *live* in a worktree, ``start`` and ``resume`` both
refuse, naming it — attach to a live background session through the harness
itself (``claude agents attach``), or stop it first.

Previewing with ``--dry``
-------------------------

Every launching command — ``goal start``/``adopt``, ``agent
start``/``resume``, ``chat``, ``review``, ``errand`` — takes ``--dry``: it
resolves everything for real (scope, harness and model, prompt, the full
rendered context and where each piece came from) and launches nothing. When
a command's behaviour surprises you, ``--dry`` is the first diagnostic.
Destructive commands have the same escape hatch — see :doc:`landing`.

Choosing harness and model
--------------------------

Sessions default to the ``claude`` harness on its own default model,
overridable per workspace or project in ``config.yaml`` and per invocation
with ``--harness``/``-m`` (see :doc:`workspace`). Anything after ``--`` on a
launching command is forwarded verbatim to the harness::

    ch agent resume -g add-greeting -- --model opus

The split happens before Chimera parses anything, so harness flags are never
mistaken for Chimera's own.

Two capabilities exist only at a human terminal. ``--dangerous`` makes the
harness's bypass-permissions mode *reachable* in the launched session (it
never switches it on), at the cost of displacing auto-accept from the mode
cycle; and destructive commands carry ``--force`` variants. Chimera-launched
agent sessions physically lack both — the flags are stripped from the
command tree they see, and bypass spellings after ``--`` are refused at
launch — so an agent cannot escalate its own permissions or force-discard
work. See :doc:`reference` for how this affects what ``ch help`` shows.

.. _guide-errands:

Errands: quick answers from another project
-------------------------------------------

``ch errand <project> "<question>"`` dispatches a one-shot, read-only agent
into another project's checkout and prints its report::

    ch errand billing "What testing framework does this project use?"

The errand gets an ephemeral goal and worktree, a read-only tool wall, and
the target project's own context; when the report is delivered the whole
apparatus is swept away. ``--out <path>`` writes the report to a file,
``--timeout <seconds>`` bounds the run, ``--keep`` preserves the branch and
worktree for inspection (clean up later with ``ch goal finish``). There is
no daemon: for concurrency, background the ``ch errand`` invocation itself.

Errands are how agents and managers read across project boundaries without
gaining the ability to write across them.
