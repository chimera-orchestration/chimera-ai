Logging
=======

Every ``ch`` action records a line to a single JSON-lines log at
``<workspace>/logs/chimera.jsonl`` (gitignored). Each line carries the canonical
command path and its parsed parameters, plus any extra structured fields the
command binds. The log is the audit trail and — for anything that rewrites git
history — the recovery backstop.

Ref safety
----------

Creating, repointing, or deleting a git ref (a branch, a tag, any named ref) is
destructive: the old value is gone the moment the ref moves. So before any such
change, ``ch`` records the affected refs and the full commit sha each points at,
then records them again afterwards. The pair is enough to undo the change by
hand — for example ``git branch <name> <sha>`` to resurrect a deleted branch.

The record is a single ``git`` field on the action's log line, holding a
``before`` and an ``after`` map of ``{ref: full-sha}``. Only refs that exist at
that moment appear; a ref missing from a map did not exist then. That makes the
operation legible from the pair alone:

============  ====================  ====================
Operation     ``before``            ``after``
============  ====================  ====================
create        ``{}``                ``{ref: sha}``
delete        ``{ref: sha}``        ``{}``
repoint       ``{ref: old-sha}``    ``{ref: new-sha}``
============  ====================  ====================

A creation thus shows an empty ``before`` and a populated ``after``; a deletion
is the mirror image; a repoint shows the ref in both with a changed sha. The
shas are always full (not abbreviated), so they remain safe to recover from.
