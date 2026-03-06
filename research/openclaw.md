# OpenClaw

Open-source self-hosted personal AI agent framework. Formerly Clawdbot, then Moltbot.
~250K GitHub stars as of early 2026 — most-starred non-aggregator project on GitHub.

- Repo: https://github.com/openclaw/openclaw
- Docs: https://clawdocs.org/

## What it does

Connects any LLM to the messaging apps you already use (WhatsApp, Telegram, Slack, Discord,
Signal, iMessage) and acts on your behalf: sends emails, calls APIs, scrapes websites, creates
files, runs recurring tasks. Proactive as well as reactive.

## Architecture (5 components)

**Gateway** — WebSocket server (default localhost:18789) that routes messages in/out across all
connected channels.

**Brain** — Model-agnostic LLM integration using the ReAct reasoning loop. Supports GPT, Claude,
Gemini, DeepSeek, Llama, etc.

**Memory** — Flat Markdown files on disk:
- `soul.md` — agent personality and persistent instructions
- `agents.md` — agent configuration
- conversation memory file — minified history for cross-session context

**Skills** — Plugin system. Each skill is a Markdown file with YAML frontmatter defining trigger
conditions and available tools. Installable from ClawHub (5,700+ community skills).

**Heartbeat** — Runs every 30 minutes. Checks conditions defined in the agent's files and acts
proactively if needed. Makes the agent autonomous rather than purely reactive.

## Key properties

- Self-hosted, local-first — all data stays on disk as readable Markdown/YAML
- Model-agnostic — swap LLMs freely
- Extensible — Skills are the plugin mechanism
- MIT licensed

## Security note

Active campaigns use fake OpenClaw GitHub repos (amplified by Bing AI search) to spread
infostealers. Only use the official repo above.
