---
description: Rebase the current branch onto local main (project rule — local, never origin/main)
argument-hint: "[base-branch]"
allowed-tools: Bash(git status:*), Bash(git rev-parse:*), Bash(git branch:*), Bash(git rebase:*), Bash(git log:*), Bash(git diff:*), Bash(./happy.sh:*)
---

Rebase the current branch onto a base branch. Base = `$ARGUMENTS`, or `main` if no argument was given.

Rules:
- Use the **local** base branch. Never `git fetch` and never use `origin/<base>` — local `main` is intentionally ahead of the remote.
- Do not rebase interactively (`-i` is unsupported here).

Steps:
1. Stop if `HEAD` is already on the base branch, or if the working tree is dirty — report why and do nothing (do not stash).
2. Run `git rebase <base>`.
3. On conflict: stop, list the conflicted files, and resolve them with the user — never auto-resolve or `--skip`. (`git rebase --abort` if they want out.)
4. On success: show `git log --oneline <base>..HEAD` so the replayed commits are visible.
5. Run `./happy.sh` and confirm exit 0 — the rebased tree must still be green.
