Logging and observability
=========================

Every ``ch`` action records what it examined, decided and changed to a single
JSON-lines log at ``<workspace>/state/log.jsonl`` (gitignored). Each
action lands a start/end pair carrying the canonical command path and its
parsed parameters, with the decisions and mutations in between — enough that
the log alone can reconstruct a run, and, for anything that rewrites git
history, undo it.

Watching and reading the log
----------------------------

``ch logtail`` is the human view: it follows the live log, colourised, with
a display format tuned to the fields that matter (command, phase, duration,
error). ``-n <N>`` sets how many existing lines to show first,
``--no-follow`` takes one look and exits, and ``-d``/``--dump`` is the
post-mortem surface — every field of every record, parameters, git
before/after maps and full tracebacks included.

It renders through `fblog <https://github.com/brocode/fblog>`_; ``ch
doctor`` checks it's installed and ``--fix`` installs it with Homebrew.

The raw file is always there too — one JSON object per line, ready for
``tail -f``, ``jq`` or ``grep``. That's deliberately the form Chimera's own
agents use: ``logtail`` is reserved for human terminals, since a blocking
follow is a dead end for an agent.

The git trace
-------------

Every git subprocess Chimera runs lands a DEBUG line *before* it executes —
so a hung fetch is on record while it hangs, exact command and working
directory included. The trace goes only to the log file, never the console.
Chimera also injects network timeouts into its git calls (unless you've set
your own), so a dead transport fails in seconds rather than hanging forever.

Launch audit
------------

Every launched session's rendered context — role directives, principles,
knowledge index — is written content-addressed under
``<workspace>/state/context/`` and logged with its hash and a map of exactly
which files fed it: the audit record of what a session was told, and of why
a directive did or didn't make it in. Errand reports log the same way on
delivery. See :doc:`concepts` for what goes into the render.

Ref safety
----------

Creating, repointing, or deleting a git ref (a branch, a tag, any named ref)
is destructive: the old value is gone the moment the ref moves. So ``ch``
records the affected refs and the full commit sha each points at, both before
and after the change, on a single log line. The pair is enough to undo the
change by hand — for example ``git branch <name> <sha>`` to resurrect a
deleted branch.

The record is a single ``git`` field on a log line (its message is the
command path suffixed ``: refs``), holding a ``before`` and an ``after`` map
of ``{ref: full-sha}``. Only refs that exist at that moment appear; a ref
missing from a map did not exist then. That makes the operation legible from
the pair alone:

============  ====================  ====================
Operation     ``before``            ``after``
============  ====================  ====================
create        ``{}``                ``{ref: sha}``
delete        ``{ref: sha}``        ``{}``
repoint       ``{ref: old-sha}``    ``{ref: new-sha}``
============  ====================  ====================

A creation thus shows an empty ``before`` and a populated ``after``; a
deletion is the mirror image; a repoint shows the ref in both with a changed
sha. The shas are always full (not abbreviated), so they remain safe to
recover from.
