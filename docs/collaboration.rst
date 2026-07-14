Mail, hooks and the archive
===========================

A working workspace soon holds several sessions at once — the captain, a
manager or two, an agent per goal — each in its own terminal or background
process. This guide covers the connective tissue between them: **mail**, how
sessions (and you) send each other messages; the **hooks** that deliver that
mail into running sessions and record every session as it starts and ends;
and the **archive**, the queryable history those hooks feed.

All of it lives under ``<workspace>/state/`` — runtime state, not
configuration, so none of it is tracked by the workspace's git (see
:doc:`workspace`).

Addresses
---------

A mailbox address *is* a session name — the naming you already know:

* the captain's persona — ``pegasus``, or whatever you named yours;
* a project's manager — ``<project>@manager``;
* a goal's agent — ``<project>@<goal>@<actor>``, e.g.
  ``demo@add-greeting@agent``.

There is no registry to maintain: a mailbox appears the first time something
writes to it, and each message is one immutable file inside it, so any
number of senders can write at once without stepping on each other.

Watching the mail
-----------------

``ch msg ls`` shows every outstanding message in the workspace — who is
asking whom what, and where each message is in its life:

.. invisible-code-block: python

    # stand up a workspace with one goal underway, then let its agent ask the
    # captain a question — what a running agent session would do for itself.
    session.run('ch init ~/lycia --captain pegasus')
    session.env['CHIMERA_WORKSPACE'] = str(session.home / 'lycia')
    session.run('ch project new demo')
    session.run('ch worktree add --goal add-greeting -p demo')
    session.cwd = session.home / 'lycia'
    session.run(
        'ch msg send pegasus "Ready to merge?" '
        '"greeting.txt is committed and tests pass - shall I merge to main?" '
        '--kind request --from demo@add-greeting@agent'
    )

.. code-block:: console

    $ ch msg ls
    new   demo@add-greeting@agent → pegasus  [request] Ready to merge?

The first column is the state: ``new`` — delivered, not yet seen by its
recipient; ``cur`` — received, awaiting an answer or an acknowledgement;
``done`` — dealt with (hidden by default; ``-v`` shows them). A message
carries a kind — ``message`` (an FYI, reply welcome), ``request`` (expects
an answer), ``escalation`` (needs attention from above), ``notice``
(fire-and-forget) — and a priority; today these are conventions that travel
with the message rather than machinery, set with ``--kind``/``--priority``
on ``send``.

Reading and replying
--------------------

``ch msg`` commands infer *whose* mailbox from where you stand, the same way
other commands infer project and goal: the workspace root is the captain's
seat, a project directory the manager's, a goal worktree its agent's. From
the workspace root, then, ``ch msg inbox`` reads the captain's mail — there
is no separate human address; you and the captain share a seat:

.. code-block:: console

    $ ch msg inbox
    20260712T091403521881-7c40d1a5  demo@add-greeting@agent  [request] Ready to merge?

``inbox`` and ``thread`` are read-only peeks — they never change a message's
state. To answer, send a message back, quoting the id you're answering with
``--re`` (which also threads the exchange, so ``ch msg thread <root-id>``
can gather the conversation later); then ``ack`` the request to retire it:

.. code-block:: console

    $ ch msg send demo@add-greeting@agent "Re: Ready to merge?" "Yes - merge it." --re 20260712T091403521881-7c40d1a5
    Sent 20260712T091447139022-3e8b90f2 to demo@add-greeting@agent
    $ ch msg ack 20260712T091403521881-7c40d1a5
    Acked 20260712T091403521881-7c40d1a5
    $ ch msg ls
    new   pegasus → demo@add-greeting@agent  [message] Re: Ready to merge?
    (+1 disposed message — ch msg ls -v to show)

Your reply went out as ``pegasus`` for the same seat-sharing reason; every
command takes an explicit address (and ``send`` a ``--from``) when you mean
someone else's mailbox, as the next section shows.

Delivery: drain, ack, defer
---------------------------

Delivery into a *running* session happens at turn boundaries: a hook (next
section) runs ``ch msg drain --inject`` as each prompt is submitted, and the
claimed messages ride into the session as context. Draining is the claim —
``new`` becomes ``cur`` — and the same command works by hand, to check what
a session that isn't running would receive, or to read mail for a seat
Chimera doesn't run (a reviewer, say):

.. code-block:: console

    $ ch msg drain demo@add-greeting@agent
    20260712T091447139022-3e8b90f2  from pegasus  [message] Re: Ready to merge?

A drained message still awaits its *disposition*: ``ack`` marks it handled,
``defer`` puts it aside with a reason (recorded in the log, like every send,
receive and disposal — see :doc:`logging`). Either way it moves to ``done``
and stops being outstanding:

.. code-block:: console

    $ ch msg defer 20260712T091447139022-3e8b90f2 demo@add-greeting@agent --reason "merging - will confirm after"
    Deferred 20260712T091447139022-3e8b90f2: merging - will confirm after
    $ ch msg ls
    No outstanding messages
    (+2 disposed messages — ch msg ls -v to show)

Installing the hooks
--------------------

Two kinds of hook make this run without anyone polling: SessionStart and
SessionEnd record every Claude session into the archive (below) as it comes
and goes, and UserPromptSubmit drains a session's mail into each turn. They
are installed user-wide — in ``~/.claude/settings.json``, not the workspace
— so they fire for *every* session on the machine, including ones you launch
yourself; that completeness is what makes the archive trustworthy.

``ch doctor`` owns the installation (there is no separate install command to
remember): its ``claude-hooks`` check reports the missing hooks, and
``--fix`` merges them in, idempotently, preserving whatever hooks and
settings you already have:

.. code-block:: console

    $ ch doctor -c claude-hooks --fix
    [claude-hooks] (fixed) /Users/you/.claude/settings.json missing chimera hooks: SessionStart, SessionEnd, UserPromptSubmit

See :doc:`health` for doctor itself.

The archive
-----------

The archive is one SQLite database, ``<workspace>/state/archive.db``,
indexing every LLM session the hooks have seen — Chimera-launched or not.
Each session is recorded with the harness that ran it and its native session
id, who orchestrated it (``chimera``, or ``none`` for one you launched
yourself), when it started and ended, its working directory and transcript
path, and — for sessions inside a managed worktree — the workspace, project,
goal and actor it served. Where the :doc:`log <logging>` records what
*happened* line by line, the archive ties happenings to sessions: which
sessions worked a goal, what ran yesterday, where a transcript lives.

.. invisible-code-block: python

    # the SessionStart hook firing as an agent session opens in the goal's
    # worktree — what a real `claude` launch does once the hooks are installed.
    import json
    import subprocess
    import sys

    payload = {
        'cwd': str(session.home / 'lycia/demo/worktrees/add-greeting@agent'),
        'session_id': '4f6b2a90-51de-4c3d-9e2a-8f7b6c5d4e3a',
        'transcript_path': str(session.home / '.claude/transcript.jsonl'),
        'source': 'startup',
    }
    subprocess.run(
        [sys.executable, '-m', 'chimera', 'hook', 'session-start'],
        input=json.dumps(payload), text=True, check=True,
        cwd=session.cwd, env=session.env,
    )

Nothing in today's ``ch help`` reads it back yet — it accrues now so the
history is there when you want it, and it is ordinary SQLite, so anything
that speaks SQL can ask:

.. code-block:: console

    $ sqlite3 ~/lycia/state/archive.db "SELECT name, project, goal, actor FROM sessions"
    demo@add-greeting@agent|demo|add-greeting|agent

The ``name`` column is the session's address — the same string ``ch msg``
routes on — so mail and history meet in the middle: the archive can tell you
who to write to, and the mailboxes hold what they said.
