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

# Recall: ask a question or pass conversation context (requires LLM)
k7e recall "SSH bastion access"
echo "We were discussing port forwarding tunnels" | k7e recall

# Consolidate duplicate nodes (dedup by title similarity)
k7e consolidate --dry-run
k7e consolidate

# Compile a topic into a reference page (requires LLM)
k7e compile networking

# Clean up
rm -rf /tmp/k7e-demo /tmp/notes.md
```

## Usage

```bash
k7e store "title" --tags x,y [--content "..." | stdin]
k7e search "query" [--limit N] [--json] [--ids] [--rerank] [--include-superseded]
k7e get K7E-000-00001
k7e supersede K7E-000-00001 K7E-000-00002
k7e append K7E-000-00001 --section "name" [--content "..." | stdin]
k7e asset screenshot.png
k7e recall "topic or conversation" [--limit N]
k7e distill file.md [--dry-run]
k7e consolidate [--dry-run]
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
k7e status                  # show what's active, incl. the resolved LLM model
k7e config llm_model qwen3:8b   # pin the ollama model (unset = auto-detect)
k7e config llm none         # disable the LLM entirely (pattern-only distill)
k7e config embed_model nomic-embed-text
k7e config ollama_url http://localhost:11434
k7e config rerank true      # LLM rerank search results (off by default)
k7e config decay_scale_days 365   # recency half-life past the flat zone
```

Config stored in `$K7E_HOME/config.json`. Env vars override: `K7E_LLM`,
`K7E_LLM_MODEL`, `EMBED_MODEL`, `OLLAMA_URL`, `K7E_RERANK`, `K7E_DECAY_OFFSET`,
`K7E_DECAY_SCALE`, `K7E_USE_WEIGHT`.

### LLM backend

k7e calls **ollama's HTTP API directly** for generation (distill, recall,
rerank, compile) — never a CLI like `l9m`/`claude`/`codex`. Those carry their
own rolling context or agent preamble, which would leak into k7e's prompts;
talking to ollama keeps every call stateless. When `llm_model` is unset, k7e
auto-detects the best installed model (qwen family preferred, largest wins) and
falls back to `qwen3:0.6b`. To see exactly which model is in use:

```bash
k7e status          # → "LLM: ollama (qwen3:8b, auto-detected) ✓"
k7e config llm_model  # → resolved model name when unset
```

### Ranking

Search fuses BM25, metadata, and embeddings via RRF, then multiplies each
score by confidence, a recency decay (gauss; flat for `decay_offset_days`,
half at `decay_scale_days` past that), and a use-count boost. Entries earn
freshness when returned by `recall` or read by `get` (index-only signal, reset
on `reindex`). `--rerank` adds an LLM cross-encoder pass over the candidate
pool; it is on by default inside `recall`.

## Capabilities

| Feature | Backend | Required? |
|---------|---------|-----------|
| Keyword search (BM25) | SQLite FTS5 | Always available |
| Semantic search | ollama embeddings | Optional — `ollama pull nomic-embed-text` |
| Distillation (LLM) | ollama | Optional — pattern extraction without |
| Recall (RAG synthesis) | ollama | Optional — falls back to raw search results |
| Consolidation (dedup) | title similarity | Always available |

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
- **ollama** — local embeddings + LLM for distill/recall/rerank/compile (`curl -fsSL https://ollama.com/install.sh | sh`)

Without ollama, k7e runs FTS5-only with pattern-based distillation — still effective for keyword recall.

## Integration

Any agent or harness can call `k7e search` (read) and `k7e store` (write).
Separation of concerns: the agent reads, the harness/surgeon writes.
