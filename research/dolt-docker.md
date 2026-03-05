# Dolt via Docker Desktop

## Question

Can beads use a `dolt sql-server` hosted in a local Docker container (Docker Desktop)
instead of the process-lifecycle shenanigans Gas Town implements?

**Answer: Yes.**

---

## What was tested

### Image

```
dolthub/dolt-sql-server:latest  (Dolt 1.83.2 as of 2026-03-05)
```

Exposed ports: `3306/tcp` (MySQL), `33060/tcp`, `7007/tcp` (MCP server).
Data volume: `/var/lib/dolt` — bind-mount from host to persist data.

### Commands run

```bash
# Pull the image
docker pull dolthub/dolt-sql-server:latest

# Start a test container with host data mount
mkdir -p /tmp/dolt-test-data
docker run -d --name dolt-test \
  -p 3307:3306 \
  -v /tmp/dolt-test-data:/var/lib/dolt \
  dolthub/dolt-sql-server:latest

# Verify dolt version inside container
docker exec dolt-test dolt version
# → dolt version 1.83.2

# Test SQL via container's dolt cli
docker exec dolt-test dolt sql -q "SELECT VERSION(); SHOW DATABASES;"
# → VERSION() = 8.0.31 (Dolt's MySQL compat layer)
# → information_schema, mysql

# Verify host can connect on port 3307 (MySQL protocol banner received)
python3 -c "
import socket; s = socket.socket(); s.settimeout(5)
s.connect(('127.0.0.1', 3307)); print(repr(s.recv(50))); s.close()
"
# → b'\x4a\x00\x00\x00\n8.0.33\x00...'  ✓ MySQL handshake received

# Init a dolt database inside the container
docker exec dolt-test sh -c "
  mkdir -p /var/lib/dolt/testdb &&
  cd /var/lib/dolt/testdb &&
  dolt init --name beads --email beads@localhost
"
# → Successfully initialized dolt data repository.

# Verify data appeared on host (owned by current user, correct perms)
ls -la /tmp/dolt-test-data/
# → .dolt/  .doltcfg/  .init_completed  config.yaml  testdb/

# Clean up
docker stop dolt-test && docker rm dolt-test
rm -rf /tmp/dolt-test-data
```

### Image behaviour

The entrypoint (`tini -- docker-entrypoint.sh`) starts `dolt sql-server` automatically.
On first run it generates `/var/lib/dolt/config.yaml` with `listener.host: 0.0.0.0`,
`listener.port: 3306`. The config file is written to the mounted volume so it persists
and is editable on the host.

---

## What this replaces

Gas Town's daemon (and what Chimera would otherwise need to reimplement) handles:

- PID file management + stale PID detection
- Port allocation with hash-derived fallback ports per project
- Per-project lock files preventing concurrent starts
- Idle monitor — a separately forked process that watches activity files and
  auto-stops/restarts the server
- Crash detection + backoff restart

With `--restart unless-stopped`, Docker Desktop handles all of this. The container
auto-restarts on crash, and Docker Desktop restarts it on machine login.

---

## Production command for Chimera

```bash
docker run -d \
  --name chimera-dolt \
  --restart unless-stopped \
  -p 3307:3306 \
  -v ~/lycia/.dolt-data:/var/lib/dolt \
  dolthub/dolt-sql-server:latest
```

This mirrors Gas Town's data layout exactly — Gas Town stores all rig databases
under `~/gt/.dolt-data/{hq,gastown,beads,wyvern,...}/`, each subdirectory a separate
Dolt database. The Docker container serves the entire mounted directory; any
subdirectory initialised with `dolt init` becomes a database.

---

## Per-project beads config

```yaml
# .beads/config.yaml
dolt:
  mode: server
  host: 127.0.0.1
  port: 3307
  user: root
```

Or via environment variables:
```bash
export BEADS_DOLT_SERVER_MODE=1
export BEADS_DOLT_SERVER_HOST=127.0.0.1
export BEADS_DOLT_SERVER_PORT=3307
export BEADS_DOLT_SERVER_USER=root
```

With `dolt.mode: server` set, beads does **not** attempt to auto-spawn a local
`dolt sql-server` process — it connects to the container directly.

---

## Initialising a new database

```bash
docker exec chimera-dolt sh -c "
  mkdir -p /var/lib/dolt/myproject &&
  cd /var/lib/dolt/myproject &&
  dolt init --name 'Your Name' --email 'you@example.com'
"
```

Alternatively, if `dolt` is installed on the host:
```bash
mkdir -p ~/lycia/.dolt-data/myproject
cd ~/lycia/.dolt-data/myproject
dolt init
# restart the container (or it picks it up automatically on next connection)
```

---

## Caveats

1. **`bd dolt push/pull` via SSH remotes** — if using `git+ssh://` remotes,
   the host still needs the `dolt` binary. For DoltHub HTTPS or local `file://`
   remotes, the container handles everything natively.

2. **Explicit server mode required** — beads' auto-start logic kicks in if
   `dolt.mode` is not set to `server`. Each project's `.beads/config.yaml` must
   opt in. A shared `~/.config/bd/config.yaml` with `dolt.mode: server` would
   cover all projects.

3. **Docker Desktop dependency** — reasonable for dev machines, but `ch doctor`
   should verify the container is running and healthy.

4. **dolt identity in container** — the container's dolt global config lives in
   `/var/lib/dolt/.doltcfg/`. Pass `--name`/`--email` to `dolt init` each time,
   or pre-seed the `.doltcfg/` directory before mounting.
