Tutorial: your first goal
=========================

This walks the whole loop once: create a workspace, add a project, set an
agent working on a goal, and land its work on ``main``. It takes about ten
minutes. Every command shown here works as typed.

It assumes you have ``ch`` on your PATH and the ``claude`` CLI installed — if
not, start with :doc:`install`.

Create a workspace
------------------

A :ref:`workspace <concept-workspace>` is the directory tree Chimera manages —
projects, goals, agent worktrees and logs all live under it. Create one
(``lycia`` is the conventional name, but any path works), naming its
*captain* — the persona you will chat with to direct work:

.. code-block:: console

    $ ch init ~/lycia --captain pegasus
    Initialized workspace at /Users/you/lycia

Then tell ``ch`` where it is, permanently, so every command works from any
directory:

.. code-block:: console

    $ export CHIMERA_WORKSPACE="$HOME/lycia"

Add that line to your shell profile too (``~/.zshrc`` or ``~/.bashrc``) —
``ch doctor`` will remind you until it is there. See :doc:`workspace` for how
commands locate the workspace without it.

Add a project
-------------

A :ref:`project <concept-project>` is one repository Chimera manages work in.
There are three ways in, and all end in the same place:

* ``ch project new demo`` — a fresh repository, local-only, no remote yet.
* ``ch project add <git-url>`` — clone an existing remote repository.
* ``ch project add <path>`` — register a checkout you already have on disk.

For the tutorial, start from nothing:

.. code-block:: console

    $ ch project new demo
    Created /Users/you/lycia/demo

The dashboard now shows it:

.. code-block:: console

    $ ch ls
    lycia
      @@captain  (never run)
      demo
        demo@@manager  (never run)
        (no goals)

Start a goal
------------

A :ref:`goal <concept-goal>` is a thing that needs doing. ``ch goal start``
creates a branch and an isolated git worktree for it, then launches an agent
there. Before running anything for real, you can always preview a launching
command with ``--dry`` — it resolves everything (branch, worktree, harness,
the context the agent will be given) but changes nothing:

.. code-block:: console

    $ ch goal start add-greeting -p demo --dry
    Would start add-greeting in /Users/you/lycia/demo/worktrees/add-greeting@agent
    harness: claude
    role: agent (scope: demo@add-greeting)
    prompt: (interactive)
    ...

Note the shape: the agent gets branch ``add-greeting/agent`` checked out in
worktree ``demo/worktrees/add-greeting@agent`` — its own copy of the
repository, so nothing it does touches yours. The rest of the preview is the
context Chimera injects at launch (see :doc:`concepts`).

Now run it for real, from anywhere (``-p demo`` names the project; inside the
project directory you could drop it):

.. interactive — not run by the doc tests; the invisible block below stands in.
.. skip: next

.. code-block:: console

    $ ch goal start add-greeting -p demo

.. invisible-code-block: python

    # goal start was skipped above (it opens an interactive agent session), so the
    # doc tests stand in for it: the same branch-and-worktree setup it performs,
    # then the commit the prose below asks the agent for.
    session.run('ch worktree add --goal add-greeting -p demo')
    worktree = session.home / 'lycia/demo/worktrees/add-greeting@agent'
    (worktree / 'greeting.txt').write_text('Hello there, and welcome!\n')
    session.run(f'git -C {worktree} add greeting.txt')
    session.run(f'git -C {worktree} commit -q -m "Add greeting"')

This opens an interactive Claude Code session in the worktree. Ask it for
something small — *"create greeting.txt containing a friendly greeting, and
commit it"* — and watch it work. Agents commit as they go, on their own
branch, so the branch always tells the story of the work.

When it is done, leave the session (``Ctrl-C`` twice or ``/exit``). You can
come back to it any time with ``ch agent resume -g add-greeting -p demo``.

.. tip::

   Give ``goal start`` a prompt — ``ch goal start add-greeting "add a friendly
   greeting" -p demo`` — and the agent launches in the *background* instead,
   working autonomously while you do something else. ``ch agent ls`` shows
   what is running; once the session has exited, ``ch agent resume`` reopens
   it to see what it did.

Inspect the work
----------------

The goal now appears on the dashboard, and the work is on the agent's branch:

.. code-block:: console

    $ ch goal ls -p demo
    add-greeting
    $ git -C ~/lycia/demo/worktrees/add-greeting@agent log --oneline
    3ada24c Add greeting
    642ba5c Empty seed commit (ch project new)

If you want your own checkout of the work — to run it, edit it, or curate the
history — sync it onto your *human* branch:

.. code-block:: console

    $ ch goal sync add-greeting -p demo
    Created human at agent (3ada24c)

Each actor in a goal (you, the agent) works on its own branch,
``<goal>/<actor>``; ``goal sync`` brings yours up to the agent's. See
:doc:`landing` for the full story, including what happens after you squash.

Land it
-------

``ch goal merge`` lands a finished goal on the default branch and sweeps
everything away — branches, worktree, and any still-running agent session.
It refuses if anything is unsafe (uncommitted changes, actors that have
diverged), so it is safe to just try:

.. code-block:: console

    $ ch goal merge add-greeting -p demo
    Fast-forwarded main to add-greeting/human (3ada24c)
    Removed /Users/you/lycia/demo/worktrees/add-greeting@agent

The commit is on ``main``; the goal is gone from ``ch ls``. If your project
lives on a remote and you'd rather review on GitHub, ``ch goal pr`` publishes
the goal as a pull request instead — see :doc:`landing`.

Talk to the captain
-------------------

You don't have to drive every step yourself. From the workspace root,
``ch chat`` launches the captain — a workspace-level agent that knows the
``ch`` commands and directs work across all projects:

.. interactive — not run by the doc tests.
.. skip: next

.. code-block:: console

    $ cd ~/lycia && ch chat

Tell pegasus what you want done and it will start goals, check on agents and
land results for you. Inside a project directory, the same command launches
that project's *manager* instead. See :doc:`directing`.

Where next
----------

* :doc:`concepts` — the vocabulary just used, properly defined.
* :doc:`directing` — chatting, starting and adopting goals, managing agents.
* :doc:`landing` — sync, merge, pull requests and reviews.
* :doc:`health` — ``ch doctor``, the workspace's health check.
* ``ch help`` — every command, one screen; see :doc:`reference`.
