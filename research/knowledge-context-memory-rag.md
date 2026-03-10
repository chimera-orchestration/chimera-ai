# Knowledge, Context, Memory & RAG

General landscape of context injection for LLM coding agents. For Chimera-specific implementation (Claude Code hooks, Chimera memory architecture), see [memory.rst](memory.rst).

---

## The problem

Coding agents need relevant context before acting. Dumping everything into the prompt wastes tokens and reduces adherence. The goal: inject the right context at the right time.

---

## Key principle: text-in / text-out

LLMs can only read and produce text. Embeddings power retrieval internally, but the interface is always:
- Query: natural language text
- Response: text chunks (code snippets, explanations, docs)

Embeddings are never exposed directly to the model — retrieval is a preprocessing step.

---

## MCP (Model Context Protocol)

A standardised bridge between AI agents and external tools / data sources.

Three phases:
1. **Discovery** — agent learns what resources and tools are available
2. **Retrieval / invocation** — agent queries a resource or calls a tool
3. **Integration** — retrieved text chunks are injected into the agent's context

MCP servers can use embeddings internally for search, but return plain text to the agent.

---

## RAG (Retrieval-Augmented Generation)

Combine LLM generation with retrieval from an external store:

1. Index documents — chunk text, embed each chunk, store (chunk + vector + metadata)
2. At query time — embed the query, find similar vectors, retrieve matching text chunks
3. Inject chunks into context — model generates response using retrieved content

**Metadata** (file, date, tag, etc.) does not affect the embedding — used only for filtering results.

---

## Embedding stores

| Project | URL | Service required | Notes |
|---|---|---|---|
| FAISS | https://github.com/facebookresearch/faiss | No — in-process | Created by Facebook AI (not Hugging Face, though HF integrates it); fast; no built-in persistence |
| ChromaDB | https://github.com/chroma-core/chroma | Typically yes | Python-native; easy local dev |
| Milvus | https://github.com/milvus-io/milvus | Yes — heavy | Production-grade distributed store |
| Qdrant | https://github.com/qdrant/qdrant | Yes (or embedded mode) | Rust; can run embedded |
| pgvector | https://github.com/pgvector/pgvector | Yes — Postgres | Good if already running Postgres |
| sqlite-vec | https://github.com/asg017/sqlite-vec | No — SQLite extension | Pure C; in-process; ~5ms queries; no service |

For a CLI tool like Chimera, **sqlite-vec** is the right default — no service, single file, SQL interface.

---

## Coding agents with built-in context retrieval

| Project | URL | Notes |
|---|---|---|
| Sourcegraph Cody | https://github.com/sourcegraph/cody | Combines code search + retrieval to inject context into AI coding sessions |

---

## RAG / orchestration frameworks

| Project | URL | Notes |
|---|---|---|
| LangChain | https://github.com/langchain-ai/langchain | Full RAG pipeline framework; heavy abstraction layer |
| LlamaIndex | https://github.com/run-llama/llama_index | Document indexing + retrieval focus |

Both are useful for rapid prototyping but add significant complexity. For a focused CLI tool, direct use of an embedding store is simpler.

---

## Claude Code memory/context integrations

Projects that integrate RAG/memory into Claude Code specifically:

| Project | URL | Mechanism | Storage | Retrieval |
|---|---|---|---|---|
| memsearch | https://github.com/zilliztech/memsearch | Hooks | Daily markdown + Milvus | Hybrid dense + BM25, 3-tier |
| mem0 | https://github.com/mem0ai/mem0 | MCP tools | Hosted / self-hosted | Semantic |
| mcp-memory-service | https://github.com/doobidoo/mcp-memory-service | MCP tools | SQLite + sqlite-vec + ONNX MiniLM | Hybrid BM25 + vector, ~5ms |
| claude-memory-mcp | https://github.com/WhenMoon-afk/claude-memory-mcp | MCP tools | JSON | KG-style, no semantic |
| Anthropic official memory | https://github.com/anthropics/claude-code | MCP tools (built-in) | JSON | KG only |
| chroma-mcp | https://github.com/chroma-core/chroma-mcp | MCP tools | ChromaDB | sentence-transformers |

See [memory.rst](memory.rst) for analysis of these and the recommended hook-based approach for Chimera.

---

## Sources

- https://code.claude.com/docs/en/memory
- https://github.com/facebookresearch/faiss
- https://github.com/chroma-core/chroma
- https://github.com/milvus-io/milvus
- https://github.com/pgvector/pgvector
- https://github.com/asg017/sqlite-vec
- https://github.com/langchain-ai/langchain
- https://github.com/run-llama/llama_index
