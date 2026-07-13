- agent-targeted docs go in @agent-docs/{topic}.md and must be terse, optimised for clarity and using few tokens
- human docs go in @docs/{topic}.rst, Sphinx-formatted.
- every example in any doc — human or agent — must be executed by the test suite via Sybil
  (wired in the root @conftest.py): a hand-verified example is drift waiting to happen. A
  genuinely unrunnable step (interactive session, network install) carries an explicit
  `.. skip:` with the reason beside it, never a silently untested block.
