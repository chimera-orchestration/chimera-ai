⏺ Yes, beads is absolutely central to the current HEAD — if anything, it's more deeply integrated than whatever blog post you're reading probably described. Here's the full picture:         
                                                                                                                                                                                              
  ---                                                                                                                                                                                         
  What beads actually is                                                                                                                                                                      
                                                                                                                                                                                              
  Beads (bd) is a separate project — a git-backed, Dolt-powered issue tracker optimized for AI agents. Gastown's internal code wraps bd CLI calls via os/exec; there's no direct library      
  dependency, it's pure subprocess invocation.                                                                                                                                                

  The bd CLI is the primitive. Gastown builds on top of it.                                                                                                                                   
                                                                                                                                                                                              
  ---                                                                                                                                                                                         
  What gastown uses beads for                                                                                                                                                                 

  Almost everything persistent lives in beads as typed issues with labels:

  ┌────────────────┬───────────────┬───────────────────────────────────────────────────┐
  │   Bead type    │     Label     │                  What it tracks                   │
  ├────────────────┼───────────────┼───────────────────────────────────────────────────┤
  │ Work tasks     │ (default)     │ The actual issues agents work on                  │
  ├────────────────┼───────────────┼───────────────────────────────────────────────────┤
  │ Agent beads    │ gt:agent      │ Agent lifecycle — one per running agent instance  │
  ├────────────────┼───────────────┼───────────────────────────────────────────────────┤
  │ Rig identity   │ gt:rig        │ Metadata about each rig (repo URL, prefix, state) │
  ├────────────────┼───────────────┼───────────────────────────────────────────────────┤
  │ Mail messages  │ type=mail     │ Inter-agent communication                         │
  ├────────────────┼───────────────┼───────────────────────────────────────────────────┤
  │ Wisp           │ ephemeral     │ Short-lived coordination messages                 │
  ├────────────────┼───────────────┼───────────────────────────────────────────────────┤
  │ ~~Role beads~~ │ ~~`gt:role`~~ │ Deprecated — now TOML config                      │
  └────────────────┴───────────────┴───────────────────────────────────────────────────┘

  Agent beads carry structured fields in the description text: role_type, rig, agent_state (spawning/working/done/stuck), hook_bead (what work item they're currently hooked to), active_mr,
  cleanup_status, notification_level. The HookBead and AgentState also have first-class database columns now (via bd slot set/clear and bd agent state commands).

  ---
  How components share beads databases

  This is the most interesting part. Beads databases are per-rig, not global, and sharing happens through a redirect file system:

  The topology

  <town>/
    .beads/                    ← town-level DB (hq-* prefixed: mayor, deacon)
    routes.jsonl               ← prefix routing table
    <rig>/
      .beads/                  ← rig-level DB (e.g., gt-* for gastown rig)
        redirect               ← may point to mayor/rig/.beads (tracked beads)
      mayor/rig/.beads/        ← canonical DB location for tracked-beads rigs
      crew/<name>/
        .beads/redirect        ← points back to rig-level .beads (or mayor/rig)
      polecats/<name>/
        .beads/redirect        ← same
      refinery/rig/
        .beads/redirect        ← same

  Every crew worker, polecat, and refinery worktree has a .beads/redirect file containing a relative path like ../../.beads or ../../mayor/rig/.beads. When bd runs in any of those
  directories, it follows the redirect to the shared rig database. So all crew members in a rig share one beads database — no sync needed, no divergence possible.

  The SetupRedirect() function is called during crew/polecat workspace creation to wire this up. It's smart enough to handle redirect chains (crew → rig → mayor/rig) and detect circular
  redirects.

  Cross-rig routing

  When an agent in one rig needs to reference a bead from another rig (e.g., a gt-* agent touching a bd-* bead), gastown reads routes.jsonl at the town level — a newline-delimited JSON file
  mapping prefixes to rig paths:

  {"prefix":"gt-","path":"gastown/mayor/rig"}
  {"prefix":"bd-","path":"beads/mayor/rig"}
  {"prefix":"hq-","path":"."}

  runWithRouting() uses this table to invoke bd in the correct rig directory for cross-prefix operations like bd slot set gt-agent-foo hook bd-task-bar.

  ---
  What's NOT in beads anymore

  The one significant thing that moved out of beads since whatever blog post you read: role definitions. They used to be stored as role beads (hq-mayor-role, hq-polecat-role, etc.) with
  their configuration in the bead description. That's now fully deprecated — beads_role.go still exists for backward compat but says "deprecated" in its package doc. Role config is now TOML
  files embedded in the binary. The agent bead's role_bead field is ignored on read and no longer written.

  Everything else — agent lifecycle, work assignment, cross-agent mail, rig identity, task graphs — is still very much in beads.
  

- so only one beeds for whole town?
- 
