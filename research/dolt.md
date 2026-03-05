## How Dolt Recommends Deploying Their Server

Three main options:

**Hosted Dolt** — Managed service on AWS/GCP. Dolt handles maintenance, uptime, backups. Similar to RDS but with version control. Best if you don't want to manage infrastructure.

**Self-hosted / on-premises** — Run `dolt sql-server` yourself. Free and open-source. You manage replication, failover, and backups. Docker image: `dolthub/dolt-sql-server`.

**MySQL hybrid** — Dolt as a MySQL replica (consuming binlogs for audit logs) or emitting binlogs to MySQL replicas. Useful for gradual adoption.

**DoltLab** — Self-hosted DoltHub for internal teams. PR-based collaboration on data without data leaving your network.

Key caveat: Dolt runs ~1.5x slower than MySQL due to version control overhead.

Reference: https://www.dolthub.com/blog/2024-08-02-dolt-deployments/

---

## Dolt Features in Gastown/Beads (Beyond Plain MySQL)

_Analysed at gastown @ 344bca85, 2026-03-05_

### Features used

1. **Commit-per-write** (`DOLT_ADD`, `DOLT_COMMIT`) — every CRUD op commits; DB treated like a git repo
2. **`dolt_log` introspection** — commit count, root/HEAD hash queries for health checks and compaction triggers
3. **`AS OF` time travel** — safe deletion: rows deleted from live DB are still accessible at any prior commit
4. **`dolt_history_*` tables** — full per-commit row history exposed as a `History()` API
5. **`dolt_diff()` table function** — structured diff between any two refs exposed as `Diff(fromRef, toRef)` API
6. **Branch manipulation for compaction** — two strategies:
   - *Flatten*: `DOLT_RESET('--soft', <root>)` + `DOLT_COMMIT` — squashes all history to one commit, safe during concurrent writes
   - *Surgical rebase*: populates and executes `dolt_rebase` table (`squash`/`pick` per commit), then swaps branches
7. **Conflict detection & resolution** — `dolt_conflicts` table + `DOLT_CONFLICTS_RESOLVE('--ours'/'--theirs')`
8. **Remote push as backup** (`DOLT_PUSH`, `dolt_remotes` table) — daemon patrol every 15 min, auto-commit + push to GitHub
9. **Federation (peer push/pull)** — `CALL DOLT_PUSH/DOLT_PULL` with credential management and conflict handling
10. **Concurrency control** — `BD_DOLT_AUTO_COMMIT=off` for concurrent polecat agents to prevent manifest contention

### Beads vs Gastown split

**Beads owns the core Dolt storage layer:**
- Transactional commits (DOLT_COMMIT inside every write transaction)
- AS OF time travel (`getIssueAsOf`)
- `dolt_history_issues` querying (History API)
- `dolt_diff()` table function (Diff API)
- Conflict detection and resolution
- Branch listing (`dolt_branches`)
- `DOLT_HASHOF('HEAD')` — current commit introspection
- Federation push/pull between peers
- Auto-commit control via env var
- `dolt_remotes` querying for doctor/fix tools
- Time-based compaction using `dolt_log` date queries

**Gastown adds the operational layer on top:**
- `dolt sql-server` process lifecycle (start/stop/health)
- `gt dolt flatten` and `gt dolt rebase` — history compaction maintenance
- Daemon-driven remote push patrol (15-min interval)
- Worklist commit wrapping (`wl claim`/`wl done`)
- Polecat auto-commit suppression (`BD_DOLT_AUTO_COMMIT=off`)

**Summary**: Beads is the Dolt storage engine — it owns versioning semantics (commits, history, diffs, time travel, conflicts, federation). Gastown owns the operational layer: server process management, history compaction, and backup push scheduling.
