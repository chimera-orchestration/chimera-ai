# Remote Chimera Access: Mosh + Tmux

## Use case

Chimera runs agents on a remote host (home server, VPS, etc.) while you work from a laptop on the move.
Goal: leave agents churning on tasks and reconnect from anywhere without interrupting them.

## Stack

```
laptop → mosh → remote host → tmux session → ch / Claude agents
```

- **Mosh** handles the transport: survives wifi drops, IP changes (home → commute → office), wakes from sleep. No reconnect dance.
- **Tmux** handles session persistence: all your `ch` processes keep running when you close the laptop lid or mosh drops. Also multiplexes into panes/windows per agent or project.

## Setup

### Remote host

```bash
# install both
apt install mosh tmux   # or brew, etc.

# mosh needs UDP ports open — default 60000–61000
# open in firewall/security group
ufw allow 60000:61000/udp
```

### Connect

```bash
# first time — create session
mosh user@remote -- tmux new -s chimera

# returning — reattach
mosh user@remote -- tmux attach -t chimera
```

Or just `mosh user@remote` and `tmux attach` manually inside.

### Recommended tmux layout

```
window: chimera
  pane 0: ch CLI / planning
  pane 1: agent 1 output
  pane 2: agent 2 output (if running parallel)
  pane 3: logs / tail
```

`tmux new-window` for separate projects.

## Agent lifecycle with this setup

1. From laptop, mosh in and `tmux attach`
2. Launch `ch` commands / agents — they run in panes
3. Close laptop / lose signal — mosh reconnects automatically; tmux session is untouched
4. On reconnect, everything is exactly where you left it
5. Agents that finished will have their output waiting in the pane

## With a VPN (e.g. Unifi Teleport)

Teleport and mosh operate at different layers and complement each other:

- **Teleport** (network layer): makes your laptop appear on the home LAN regardless of location. Remote host is reachable by local IP; no ports need to be exposed to the public internet.
- **Mosh** (application layer): terminal resilience on top — handles VPN drops/reconnects transparently, sleep/wake gaps, latency prediction.

With Teleport active:
- Mosh to the LAN IP rather than a public IP
- No need to open SSH (22) or mosh UDP range (60000–61000) publicly — everything tunnels through the VPN
- If Teleport itself drops and reconnects, mosh picks up the session without interruption

## Considerations

- **Mosh doesn't forward SSH agent by default** — use `mosh --ssh="ssh -A"` if agents need git push access
- **Scrollback**: mosh has limited scrollback; use tmux scrollback (`prefix + [`) instead
- **UTF-8**: mosh requires UTF-8 locale on both ends — usually fine on modern systems
- **Port**: mosh uses SSH (22) for handshake then switches to UDP; only UDP range needs firewall rule
- **Multiple laptops**: just mosh in from each — tmux session is shared, be careful of split-brain editing

---

## Two-workspace sync (brief notes)

When running a local workspace (laptop) and a remote workspace (server), both git-backed:

### What wants to be shared

| Thing | Notes |
|---|---|
| **Knowledge** | Named, versioned context blobs — ideal to share; same content needed everywhere |
| **Principles** | Process/step context — same argument; version-controlled, should be identical |
| **Processes** | `~/lycia/processes/` — shared definitions, could live in a shared git repo |
| **Beads / issues** | `~/lycia/.beads/` — currently per-workspace; sharing needs thought (see below) |
| **Project worktrees** | Local to each workspace — worktrees are ephemeral, not worth syncing |

### Sharing knowledge & principles

Simplest approach: **a dedicated git repo** (e.g. `chimera-knowledge`) that both workspaces pull from.
- `processes/` and knowledge files live there
- Each workspace does `git pull` before a session
- Agents could auto-pull on task start

Alternative: symlink `~/lycia/processes` to a repo that's shared (submodule or standalone).

### Beads / issue tracking

Beads are Dolt-backed (per research/beads-routing.md) — more complex to sync than plain git.
Options:
- Run a single remote Dolt instance; both workspaces connect to it (network dependency)
- Accept divergence; only sync periodically via Dolt's built-in remote sync
- Keep beads on the remote host as the source of truth; laptop workspace is read-only for issues

### Practical starting point

1. Keep Chimera's agent work on the remote host (source of truth)
2. Use laptop only for SSH/mosh access — no local workspace initially
3. When you need a local workspace, sync knowledge/principles via a shared git repo
4. Defer beads sync until it's actually painful — Dolt remote sync or just live with remote-only
