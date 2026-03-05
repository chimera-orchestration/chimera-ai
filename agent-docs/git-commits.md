- before committing, run @happy.sh. return code MUST be 0 and output MUST be error-free.
- Good commit messages are succinct and describe why not what, the commit itself contains the "what".
- One logical change per commit — never bundle unrelated changes.
- When uncommitted changes span multiple logical changes, use `git add -p` to stage hunks selectively.
- Never announce or ask before running git commands — just do them.
- At task end: make semantic commits, never leave uncommitted files.

## Branching

- Always work on a branch, never directly on `main`.
- Rebase on **local `main`** (not `origin/main`) — local main is often ahead of the remote:
  `git rebase main`
