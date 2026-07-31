# Environment variables

Read before adding, reading or removing one. The whole list of variables **chimera owns** —
there are three, and that is the point: identity lives in the archive, not in the environment,
because an environment reaches a foreground session and nothing else (see `sessions.md`,
*Environment inheritance — exactly one hop*).

| variable | set by | read by | absent |
|---|---|---|---|
| `CHIMERA_WORKSPACE` | the user's shell profile (doctor's `workspace-env` check prints the line) | `context.resolve_workspace`, and so every command | walk up from cwd to the nearest `kind: workspace` |
| `_CH_COMPLETE` | the shell, per TAB | `chimera.git.completing` (mutes the git DEBUG trace), doctor's `shell-completion` check | not completing |
| `_CHIMERA_COMPLETE` | as above, for the `chimera` entry point | as above | as above |

`_CH_COMPLETE`/`_CHIMERA_COMPLETE` are Click's, named after chimera's own console scripts.
Chimera never sets them; it recognises them, because a completer must never print — and a
stray DEBUG line would corrupt the completion stream.

## Not ours

Read but not owned, and deliberately absent from the table above:

- **`SHELL`** — doctor's `shell-completion` check, to know which profile to name.
- **`GIT_*`** — `chimera.git` injects `GIT_SSH_COMMAND` and the `GIT_HTTP_LOW_SPEED_*` pair
  *only when the user has set none*, so a dead transport fails in seconds rather than hanging;
  `happy.sh` pins `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` to `/dev/null` so tests never see the
  machine's git identity. Git's, not chimera's.
- **the harness's own** — `CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_ENTRYPOINT`,
  `AI_AGENT`, `$CLAUDE_ENV_FILE`. `sessions.md` owns these: it version-stamps what is observed
  versus documented, which is exactly the context that makes them safe to rely on. One
  cross-reference, never a second copy that can drift.

## Retired

Named here so a reader who meets one in an old log or an old branch knows what happened:

- **`CHIMERA_ROLE`, `CHIMERA_ROLE_SCOPE`** — stamped a session's role and fence into its
  environment. Replaced by the session's **address**, which encodes both and survives a
  background launch, a bridge and a resume; the stamp survived none of those.
- **`CHIMERA_SESSION`** — an address stamp read by the log's `caller`. It was never written
  anywhere in `src/`; `chimera.identity.executor` now answers the same question from evidence.
