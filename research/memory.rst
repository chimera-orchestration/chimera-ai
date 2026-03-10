  ---
  Memory for Claude Code: What's Efficient

  The core injection mechanism: hooks

  This is the key architectural finding. Claude Code has two hook events that can inject text directly into my context, silently, before I process anything:

  - SessionStart → inject context at session start
  - UserPromptSubmit → receives the prompt text, can do semantic search against it, inject relevant memories as additionalContext

  UserPromptSubmit is the sweet spot for chimera: it fires on every prompt, receives the actual text, and can return relevant memories as invisible context before I ever respond. UserPromptSubmit has NO matcher support — it always fires on every prompt.

  There's also Stop for the write side - capture session learnings and store them.

  Hook types (four available): command, http, prompt, agent. The prompt type is especially useful for Stop — run Haiku inline without a separate script.

  Hook config file locations (all merge):
  - ~/.claude/settings.json — global (all projects)
  - .claude/settings.json — project-level (committable)
  - .claude/settings.local.json — project-level (gitignored)

  additionalContext exact JSON structure:

    {
      "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": "<text injected before Claude responds>"
      }
    }

  Multiple hooks' additionalContext values are concatenated. Exit code semantics:
  - Exit 0: success, parse JSON from stdout
  - Exit 2: blocking — stderr is fed to Claude as an error message
  - Other: non-blocking, shown only in verbose mode

  Can I read embeddings directly?

  No. I'm purely text-in, text-out. The embedding/retrieval layer has to be a script or MCP server that does the lookup and hands me back text chunks. This is fine - it just means the architecture is: store → retrieve →
  inject text → I use it.

  The landscape

  Two integration patterns:

  1. MCP tools - I explicitly call search_memory(...) when I think I need it. Requires me to decide to use the tool. Less automatic.
  2. Hooks - retrieval happens transparently on every prompt without me having to decide. More reliable for ambient memory.

  Existing projects worth knowing about:

  ┌───────────────────────────────────────────────────────┬───────────────┬───────────────────────┬──────────────────────────────┬──────────────────────────────────────────────────────────────────────┐
  │                        Project                        │   Approach    │        Storage        │          Embeddings          │                                Notes                                 │
  ├───────────────────────────────────────────────────────┼───────────────┼───────────────────────┼──────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
  │ https://zilliztech.github.io/memsearch/claude-plugin/ │ Hooks + skill │ Markdown files        │ Milvus (hybrid BM25 + dense) │ Most aligned with what you're describing - a full Claude Code plugin │
  ├───────────────────────────────────────────────────────┼───────────────┼───────────────────────┼──────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
  │ https://mem0.ai/blog/claude-code-memory               │ MCP tools     │ Hosted or self-hosted │ Yes (OpenAI or local)        │ Auto dedup/summarization, 10k free memories/mo                       │
  ├───────────────────────────────────────────────────────┼───────────────┼───────────────────────┼──────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
  │ https://github.com/WhenMoon-afk/claude-memory-mcp     │ MCP tools     │ JSON                  │ No                           │ Simple KG-style                                                      │
  ├───────────────────────────────────────────────────────┼───────────────┼───────────────────────┼──────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
  │ https://github.com/doobidoo/mcp-memory-service        │ MCP tools     │ SQLite                │ ONNX local (MiniLM)          │ Hybrid BM25 + vector, self-hosted, 5ms search                        │
  ├───────────────────────────────────────────────────────┼───────────────┼───────────────────────┼──────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
  │ Anthropic memory server                               │ MCP tools     │ JSON                  │ None (KG only)               │ Official but no semantic search                                      │
  ├───────────────────────────────────────────────────────┼───────────────┼───────────────────────┼──────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
  │ chroma-mcp                                            │ MCP tools     │ ChromaDB              │ sentence-transformers        │ Fully local                                                          │
  └───────────────────────────────────────────────────────┴───────────────┴───────────────────────┴──────────────────────────────┴──────────────────────────────────────────────────────────────────────┘

  What chimera should build

  The memsearch approach is closest to optimal: hooks for automatic injection + vector search for retrieval. But you could go simpler without sacrificing much:

  Tier 1 (no embedding infra, surprisingly effective):
  - Stop hook: summarize session with Claude Haiku, append to ~/.chimera/memory/YYYY-MM-DD.md
  - UserPromptSubmit hook: BM25/grep search over recent memory files, inject top N results as additionalContext
  - No external deps, no models to run, works offline

  Tier 2 (proper semantic recall):
  - Same hook architecture but storage is SQLite + https://github.com/asg017/sqlite-vec (single file, no server)
  - Embed with a local ONNX model (MiniLM-L6-v2 runs fast on CPU, ~90MB)
  - At UserPromptSubmit: embed the prompt → cosine search → inject top K chunks

  Two-level memory (the key design choice):
  - Global behavior memory (~/.chimera/): how the user prefers to work, cross-project patterns, preferences - always injected, small
  - Project memory (.chimera/ in repo): architecture decisions, codebase-specific knowledge, gotchas - injected when in that project

  The Stop summarization step is where chimera can be smart: rather than dumping raw transcript, a Haiku prompt hook can extract only what's worth remembering (decisions made, patterns noticed, corrections given) and discard
  the noise.

  What I can't do

  - I can't pull from a vector DB myself without an MCP server or being handed the results via hook
  - Embeddings must be generated outside me (the hook script or MCP server does it)
  - additionalContext has a https://github.com/anthropics/claude-code/issues/14281 where it can get injected multiple times - worth tracking

  Bottom line for chimera

  The hooks system is the right primitive. You don't need mem0 or an external service - a UserPromptSubmit hook script that does BM25 search over markdown files is already much better than what exists today. The upgrade path
  to real embeddings (sqlite-vec + ONNX) is clean and additive. The MCP approach (me calling tools explicitly) is a nice complement for when I want to actively query memory, but hooks give you automatic ambient memory with
  zero effort on my part.

  Sources:
  - https://code.claude.com/docs/en/hooks
  - https://zilliztech.github.io/memsearch/claude-plugin/
  - https://github.com/doobidoo/mcp-memory-service
  - https://mem0.ai/blog/claude-code-memory
  - https://github.com/anthropics/claude-code/issues/14281

✻ Churned for 1m 53s

⏺ Agent "Research Claude Code memory solutions" completed

⏺ Both agents ran into permission walls but their training-knowledge summaries align with what I found via direct research. My answer above covers the full picture - nothing new to add from those agents.

  One thing I'd flag as particularly interesting for chimera's design: the Stop hook as a "prompt hook" (not a command hook) is especially clean - you can have Claude Haiku evaluate whether anything in the session is worth
  remembering and format it for storage, without needing to parse raw transcripts. That's a very low-infrastructure way to get high-quality memory writes.
