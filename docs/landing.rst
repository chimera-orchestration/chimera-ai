Landing work
============

An agent commits as it goes on its own branch, ``<goal>/agent``. This guide
covers everything from there to done: getting the work onto your branch,
landing it locally or publishing it as a pull request, reviewing other
people's PRs with an agent's help, and cleaning up.

A safety note first: every destructive command here refuses when anything
looks unsafe — uncommitted changes, unmerged work, a live agent session —
and every one takes ``--dry``, which runs all the discovery and safety
checks and mutates nothing. ``--dry`` shares the real code path, so what it
reports is exactly what a real run would do. Trying a command is therefore
cheap: the worst case is a refusal telling you why.

Syncing: your branch catches up
-------------------------------

``ch goal sync <goal>`` brings your ``human`` branch up to the agent's work,
creating it if it doesn't exist yet:

.. invisible-code-block: python

    # stand up what the tutorial built: a workspace, the demo project, and the
    # agent's greeting commit — then run this guide's commands from the project
    # directory, which is why they need no -p.
    session.run('ch init ~/lycia --captain pegasus')
    session.env['CHIMERA_WORKSPACE'] = str(session.home / 'lycia')
    session.run('ch project new demo')
    session.run('ch worktree add --goal add-greeting -p demo')
    worktree = session.home / 'lycia/demo/worktrees/add-greeting@agent'
    (worktree / 'greeting.txt').write_text('Hello there, and welcome!\n')
    session.run(f'git -C {worktree} add greeting.txt')
    session.run(f'git -C {worktree} commit -q -m "Add greeting"')
    session.cwd = session.home / 'lycia/demo'

.. code-block:: console

    $ ch goal sync add-greeting
    Created human at agent (3ada24c)

When the two branches share history it simply fast-forwards (moving your
checkout too, if you have one and it's clean). The interesting case comes
after you *squash*: your branch and the agent's have diverged, but Chimera
remembers the last agent commit it integrated and cherry-picks only what the
agent has done since — so a curated human history keeps absorbing new agent
work without replaying what you already folded in. A conflict is left in
your checkout to resolve with ``git cherry-pick --continue``, exactly as git
left it.

``--move``/``--to`` generalise the direction: any actor branch can catch up
to any other. ``--force`` (human-only) resolves a divergence the blunt way —
repoint your branch onto the agent's tip, discarding your own commits (their
shas are logged, so they're recoverable) — for when the agent rebased or
there's nothing worth replaying.

If you run ``goal sync`` from a clean plain checkout of the project's
repository, it finishes by checking the synced branch out there — you land
*on* the work in one command.

Landing locally: ``goal merge``
-------------------------------

``ch goal merge <goal>`` is the finish-up for work that doesn't need a pull
request: it fast-forwards the default branch (or ``--into <branch>``) to the
goal's work, stops any live agent sessions, and sweeps the goal's branches
and worktrees:

.. code-block:: console

    $ ch goal merge add-greeting
    Fast-forwarded main to add-greeting/human (3ada24c)
    Removed /Users/you/lycia/demo/worktrees/add-greeting@agent

It picks the goal's *source* branch itself — the actor branch that contains
every other actor's work, preferring your curated ``human`` branch when the
tips are equivalent. Everything refuses before anything moves: actors that
have diverged (``goal sync`` first), a dirty worktree, a base branch with
commits the goal doesn't have (rebase the goal's worktree onto it first —
deliberately never automated, and never forced: ``--force`` covers
discarding *goal-side* work, never the base's own commits). It is
idempotent: re-run after a half-done landing and it carries on with the
cleanup.

Publishing: ``goal pr``
-----------------------

``ch goal pr <goal>`` is ``goal merge``'s remote sibling: the same source
selection, but the work is pushed to ``origin`` as branch ``<goal>`` (the
actor suffix is local plumbing; the goal name is the publication) and a pull
request opened via ``gh``. Nothing local is stopped or deleted — the goal
keeps working until the PR lands, then ``goal merge`` or ``goal finish``
cleans up as usual.

A single-commit branch reuses its commit message as the PR title and body. A
multi-commit branch's description is written by a small model, driven by the
project's ``prompts/pr.md`` template when present — the place to encode your
team's PR conventions. ``--dry`` previews the exact title and body that a
real run will ship, byte for byte. Re-running re-pushes and reports an
already-open PR rather than duplicating it. ``--draft`` opens a draft.

A workspace-only project (no ``origin``) refuses up front, pointing at
``ch project push <url>`` to publish the repository first.

Reviewing a PR: ``ch review``
-----------------------------

``ch review <PR-number-or-url>`` works the other direction: it stands a goal
up *from* a pull request and launches a pre-human review agent on it. The PR
ref is fetched and verified against what GitHub reports, branches
``pr-<N>/{human,agent}`` are created from its head, and the agent is
launched with the project's ``prompts/review.md`` (or a packaged default) —
always behind a hardcoded guardrail forbidding it to post to the PR:
publishing an opinion stays your call. Your clean checkout, if you're in
one, is landed on ``pr-<N>/human`` so you can read the code alongside the
agent's review. ``--no-agent`` does the checkout plumbing and stops there.

Any review tool's URL that embeds the repository and PR number works, not
just GitHub's own.

One step of that prompt is a per-PR judgement call — *how* the diff gets
gathered and read — so it has its own knob. ``--review`` replaces that step,
leaving the rest of the template (the orientation, the write-up instructions)
alone:

.. illustrative — not run by the doc tests: it needs a real pull request on a
   real remote, and it launches an interactive agent.
.. skip: next

.. code-block:: console

    $ ch review 7 --review 'Run `/security-review`, then read the migration by hand.'

Unpassed, that step reads ``Run /review to gather the PR's diff and produce
findings.`` Everything else in the prompt comes from the template — which you
can take over.

Tuning the templates: ``ch prompt``
-----------------------------------

``review`` and ``pr`` are templates chimera ships and your project can own.
``ch prompt ls`` shows which is which:

.. code-block:: console

    $ ch prompt ls
    pr       .../chimera/prompts/pr.md (packaged)
    review   .../chimera/prompts/review.md (packaged)

``ch prompt show <name>`` prints one with its file and every ``$hole`` it
fills, each with the value it renders as — so a default is something you can
read rather than something buried in the code:

.. code-block:: console

    $ ch prompt show review
    source: .../chimera/prompts/review.md (packaged)
      ch prompt init review copies it into the project to edit
    ...
    substitutions:
      $PR = <the pull request number>
    ...
      $REVIEW = Run `/review` to gather the PR's diff and produce findings.  (--review)

To make one yours, copy it into the project and edit it. ``ch prompt init``
never overwrites an existing copy; ``ch prompt edit`` does the same thing and
then opens your ``$VISUAL``/``$EDITOR``:

.. code-block:: console

    $ ch prompt init review
    Created /Users/you/lycia/demo/prompts/review.md
    $ ch prompt ls
    pr       .../chimera/prompts/pr.md (packaged)
    review   /Users/you/lycia/demo/prompts/review.md

An override wins whole — nothing is merged — which is why copying beats
writing one from scratch. Drop a hole you don't want and it simply stops
being substituted; drop ``$REVIEW`` and ``ch review --review`` refuses rather
than silently doing nothing.

Renaming and finishing
----------------------

``ch goal rename <old> <new>`` renames a goal everywhere local — branches,
worktrees, sync state — carrying checkouts along, and warns about (never
touches) remote branches. ``ch goal finish <goal>`` sweeps a goal's branches
and worktrees *without* landing anything — the exit for work that is merged,
abandoned, or living on in a PR. It discovers every actor in the goal's
namespace, not just the standard pair, and applies the same clean-and-merged
safety checks per branch; ``--force`` (human-only) discards unmerged work
and skips the live-agent check.

Everything these commands do to a git ref is logged with full before and
after commit hashes — see :doc:`logging` — so even a forced sweep is
recoverable from the log.
