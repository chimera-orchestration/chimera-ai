⏺ Yes, .beads/redirect is a beads thing. It's a simple text file containing a path — when beads opens a .beads/ directory and finds a redirect file, it follows it to a different .beads/     
  directory instead. One level only, no chains.
                                                                                                                                                                                              
  It's basically an indirection mechanism — useful for worktrees or routing scenarios where you want a lightweight local .beads/ that just points elsewhere (e.g. to a shared database, or the
   main worktree's .beads/). The doctor even checks that redirect files aren't accidentally committed to git and that their targets exist and are valid.      
  

If this repo is one you contribute to regularly, you might want a repo-local .git/info/exclude 
