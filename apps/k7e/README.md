# k7e — Knowledge

Standalone knowledge accumulation engine. Flat markdown files + hybrid search.

## Quick Start

Try this in a terminal (uses a temp store so nothing is polluted):

```bash
export K7E_HOME=/tmp/k7e-demo

# Store some knowledge
k7e store "SSH Local Forwarding" --tags ssh,networking \
  --content "ssh -L 8080:target:80 bastion — forwards local:8080 to target:80 via bastion"

k7e store "SSH Remote Forwarding" --tags ssh,networking \
  --content "ssh -R 9090:localhost:3000 server — exposes local:3000 as server:9090"

k7e store "Docker Port Mapping" --tags docker,networking \
  --content "docker run -p 8080:80 nginx — maps host:8080 to container:80"

# Search by keyword
k7e search "forwarding"

# Search by concept
k7e search "expose local service remotely"

# Read a full entry
k7e get K7E-000-00002

# Append new information to an existing entry
k7e append K7E-000-00001 --section "Edge Cases" \
  --content "Requires SSH key or password auth. Fails silently if port is in use."

# See what you've built
k7e stats
k7e list
k7e check

# Distill knowledge from a raw file
echo "TIL: Use ssh -J for ProxyJump, cleaner than -L for bastion hopping" > /tmp/notes.md
k7e distill /tmp/notes.md

# Search for the distilled knowledge
k7e search "ProxyJump bastion"

# Compile a topic into a reference page (requires LLM)
k7e compile networking

# Clean up
rm -rf /tmp/k7e-demo /tmp/notes.md
```

## Usage

```bash
k7e store "title" --tags x,y [--content "..." | stdin]
k7e search "query" [--limit N] [--json] [--ids]
k7e get K7E-000-00001
k7e append K7E-000-00001 --section "name" [--content "..." | stdin]
k7e asset screenshot.png
k7e distill file.md [--dry-run]
k7e compile <tag> [--dry-run]
k7e reindex [--embeddings]
k7e embed-pending
k7e rebuild-mocs
k7e stats [--json]
k7e check [--fix]
k7e list [--tag x] [--status active] [--ids]
k7e status
k7e config <key> [value]
```

## Storage

`K7E_HOME` env var (default: `~/.k7e`):
```
$K7E_HOME/
├── nodes/BBB/   # Bucketed atomic markdown entries (source of truth)
├── mocs/        # Maps of Content (mutable topic indexes)
├── assets/XX/   # Bucketed content-addressed binaries (SHA256 deduped)
└── .index.db    # SQLite FTS5 + embeddings (derived, rebuildable)
```

Files are truth. Index is cache. `k7e reindex` rebuilds from scratch.

## Entry format

```markdown
---
id: K7E-000-00001
title: SSH Local Forwarding
aliases: [ssh-tunnel, port-forward]
status: active
confidence: 0.5
verification_count: 0
last_updated: 2026-05-20
tags: [ssh, networking]
---

## Verified Protocol
ssh -L 8080:target:80 bastion — forwards local:8080 to target:80 via bastion

## Edge Cases
## False Paths
## History
* 2026-05-20: Initial entry.
```

## Configuration

```bash
k7e status                  # show what's available + recommendations
k7e config llm gemini       # set distillation LLM (gemini|claude|codex|ollama|auto)
k7e config embed_model nomic-embed-text
k7e config ollama_url http://localhost:11434
```

Config stored in `$K7E_HOME/config.json`. Env vars override: `K7E_LLM`, `EMBED_MODEL`, `OLLAMA_URL`.

## Capabilities

| Feature | Backend | Required? |
|---------|---------|-----------|
| Keyword search (BM25) | SQLite FTS5 | Always available |
| Semantic search | ollama embeddings | Optional — `ollama pull nomic-embed-text` |
| Distillation (LLM) | gemini/claude/codex/ollama | Optional — pattern extraction without |

`k7e status` tells you exactly what's active and what to install for full capability.

## Tests

```bash
cd ~/bin/apps/k7e
tests/run           # 69 tests, ~20s
tests/run -v        # verbose
tests/run -k dedup  # filter
tests/run -m "not slow"  # skip slow tests
```

## Dependencies

Required: Python 3.10+, sqlite3 (bundled).

Optional:
- **ollama** — local embeddings + fallback LLM (`curl -fsSL https://ollama.com/install.sh | sh`)
- **LLM CLI** — one of: gemini, claude, codex (for distillation + compilation)

On slim machines without GPU: use a cloud LLM CLI for distillation and skip local embeddings (FTS5-only mode is still effective).

## Integration

Any agent or harness can call `k7e search` (read) and `k7e store` (write).
Separation of concerns: the agent reads, the harness/surgeon writes.
