Keeping the workspace healthy
=============================

``ch doctor`` is the workspace's health check: a registry of independent
checks, each of which knows how to spot one kind of drift — a missing
configuration marker, a leftover worktree, a stale registration, an
out-of-date Chimera checkout, missing shell completion, session hooks not
yet installed (see :doc:`collaboration`) — and, where it's safe, how to
repair it.

.. illustrative — not run by the doc tests: doctor's report is machine- and
   network-dependent by design (it checks this very chimera checkout against
   origin, brew-installed tools, your shell), so its output can't be pinned.
.. skip: next

.. code-block:: console

    $ ch doctor
    [captain] (needs attention) /Users/you/lycia/roles/captain has no *.md directive files for the captain role
    [workspace-clean] (would fix — run with --fix) /Users/you/lycia has uncommitted changes
    (+13 checks passed — ch doctor -v to list)

Each finding names its check and states one of three things: it *would fix*
this with ``--fix``; it was *fixed* (on a ``--fix`` run); or it *needs
attention* — a repair that could lose work, or one Chimera refuses to make
for you (it never edits your shell profile, for instance — those findings
print the exact line to add instead). ``doctor`` exits non-zero while any
finding remains unresolved, so it slots straight into scripts and CI.

The knobs:

``--fix``
    apply the repairs instead of only reporting. Fixes that touch git refs
    log full before/after hashes first (see :doc:`logging`), and anything
    risky is reported rather than forced.

``-v``, ``--verbose``
    also show the checks that pass — the full picture rather than just the
    problems.

``-c``, ``--check <name>``
    run only the named checks (repeatable, tab-completes) — fix one problem
    while leaving the rest alone.

``-x``, ``--exclude <text>``
    skip findings whose check name equals, or message contains, the text
    (repeatable) — mute a known in-flight situation while everything else
    still reports and fixes. An excluded finding is never fixed and doesn't
    fail the exit code.

The check registry evolves with the layout it guards; don't memorise it —
``ch doctor -v`` on your own workspace is the current list, each check named
and self-describing. Run ``ch doctor`` whenever something feels off, after
upgrading Chimera, or when a git GUI has been anywhere near the worktrees;
it is safe by construction, since without ``--fix`` it only reports.
