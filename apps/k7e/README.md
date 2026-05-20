# k7e — Knowledge

Standalone knowledge accumulation engine. Flat markdown files + hybrid search.

## Usage

```bash
k7e store "Chrome Remote Debugging" --tags browser,playwright <<< "Use --remote-debugging-port=9222"
k7e search "chrome debugging"
k7e get KG-00001
k7e tend KG-00001 --section "Edge Cases" <<< "Port 9222 conflicts with other tools"
k7e asset screenshot.png
k7e consolidate journal/today.md
k7e reindex --embeddings
k7e stats
k7e check --fix
```

## Storage

`K7E_HOME` env var (default: `~/.k7e`):
```
$K7E_HOME/
├── nodes/       # Atomic markdown entries (source of truth)
├── mocs/        # Maps of Content (mutable topic indexes)
├── assets/      # Content-addressed binaries (deduped by SHA256)
└── .index.db    # SQLite FTS5 + embeddings (derived, rebuildable)
```

Files are truth. Index is cache. `k7e reindex` rebuilds from scratch.

## Entry format

```markdown
---
id: KG-00001
title: Chrome Remote Debugging
aliases: [chrome-remote-debug]
status: active
confidence: 0.5
verification_count: 0
last_tended: 2026-05-20
tags: [browser, playwright]
---

## Verified Protocol
[content]

## Edge Cases
## False Paths
## History
```

## Dependencies

Required: Python 3.10+, sqlite3 (bundled).
Optional: ollama + nomic-embed-text (for semantic search track).

## Integration

Any agent or harness can call `k7e search` (read) and `k7e store` (write).
Separation of concerns: the agent reads, the harness/surgeon writes.
