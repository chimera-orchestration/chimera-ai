⏺ Yes, .beads/redirect is a beads thing. It's a simple text file containing a path — when beads opens a .beads/ directory and finds a redirect file, it follows it to a different .beads/     
  directory instead. One level only, no chains.
                                                                                                                                                                                              
  It's basically an indirection mechanism — useful for worktrees or routing scenarios where you want a lightweight local .beads/ that just points elsewhere (e.g. to a shared database, or the
   main worktree's .beads/). The doctor even checks that redirect files aren't accidentally committed to git and that their targets exist and are valid.      
  

If this repo is one you contribute to regularly, you might want a repo-local .git/info/exclude 

⏺ Here's what bd setup claude does:                                                                                                                                                                          
                       
  What it installs: Registers two Claude Code hooks in either ~/.claude/settings.json (global) or .claude/settings.local.json (project-level):                                                               
                                                                                                                                                                                                             
  - SessionStart → runs bd prime                                                                                                                                                                             
  - PreCompact → runs bd prime                                                                                                                                                                               
                             
  What bd prime does (the thing those hooks invoke):

  It outputs a markdown "workflow context" blob into the Claude Code session. The content adapts based on context:

  - MCP mode (if beads MCP server is configured): ~50-token minimal reminder
  - CLI mode (no MCP): ~1-2k token full reference with commands, session close protocol, etc.

  The output includes:
  - A "SESSION CLOSE PROTOCOL" checklist (e.g. git status → git add → bd sync → git commit → git push)
  - Core rules reminding the AI to use bd for task tracking instead of TodoWrite/TaskCreate
  - Essential command reference

  The PreCompact hook is the clever part — when Claude Code compacts the context window, bd prime re-injects the workflow instructions so the AI doesn't "forget" how to use beads after compaction.

  There's also a --stealth variant that skips the git operations and only does bd sync --flush-only, and you can fully override the output by dropping a .beads/PRIME.md file.

⏺ Beads Server Management (Chimera)                                                                                                                                                                        
                                                                                                                                                                                                           
  To run beads, chimera needs a management layer equivalent to gastown's daemon. Required responsibilities:                                                                                                
                                         
  Must Have                                                                                                                                                                                                
                             
  - Dolt SQL server lifecycle — start dolt sql-server, health-check every ~30s, auto-restart with backoff on crash
  - Per-write Dolt commits — beads handles this itself; just needs the server running

  Strongly Recommended (Durability)

  - JSONL → git export (every ~15 min) — exports scrubbed issue data to a git-backed archive; this is the proven disaster-recovery path, independent of Dolt itself
  - Dolt remote push (every ~15 min) — pushes Dolt commit history to a configured remote
  - History compaction (every ~24h) — squashes old commits and runs dolt_gc() to prevent unbounded growth

  Optional

  - iCloud/cloud rsync of the Dolt backup directory
  - Federation sync — peer-to-peer replication to other Dolt servers (beads handles this, not the management layer)

  Notes

  - All rig databases share one dolt sql-server process; each rig database must be explicitly initialized (CREATE DATABASE or dolt init) before use
  - The JSONL export scrubs ephemeral data (wisps, agents, messages) and includes spike detection — worth porting carefully, not just copying naively
  - Without the management layer, beads still works and writes Dolt commits, but there is no backup, no compaction, and the commit history grows forever
