# Memory System

The memory system gives an organism the ability to accumulate experience across spark cycles. Without memory, every cycle starts from zero — the organism learns nothing.

## The Hippocampus

The hippocampus is a memory organ. It stores memories in SQLite with full-text search (FTS5), deduplicates them by content hash, and consolidates over time.

The hippocampus runs on cadence like any organ. Each cycle:

1. **Consume** — read stimulus for `remember:` commands, store as memories
2. **Consolidate** — prune low-value memories when the database grows too large
3. **Report** — write health status (total memory count)

## Storing Memories

Any organ can send a memory through the nervous system:

```bash
stimulus send hippocampus "remember: the stomach ate meal 3"
stimulus send hippocampus "remember important: learned to swim faster"
stimulus send hippocampus "remember critical: human fed me for the first time"
```

Importance levels:
- `remember:` — importance 5 (default)
- `remember important:` — importance 8
- `remember critical:` — importance 10
- `remember <category>:` — custom category, importance 5

Each memory is deduplicated by SHA-256 content hash. Storing the same content twice updates the access timestamp instead of creating a duplicate.

## Recalling Memories

The brain (or any organ on the same body part) reads `memory.db` directly. This is the high-bandwidth path — no network, no stimulus delay, just SQLite on the local filesystem.

```bash
# CLI interface
memory search "food"        # FTS5 full-text search
memory recent 5             # last 5 memories
memory stats                # count, categories, avg importance
```

For remote body parts (future), the pattern is: send a query stimulus to the hippocampus, include your organ type so the hippocampus can send the results back. This is the low-bandwidth path — eventually consistent, one cycle of latency.

## Schema

```sql
memories (
    id              INTEGER PRIMARY KEY,
    content         TEXT NOT NULL,
    importance      INTEGER DEFAULT 5,     -- 1-10
    category        TEXT DEFAULT 'general',
    created_at      TEXT NOT NULL,
    accessed_at     TEXT,
    access_count    INTEGER DEFAULT 0,
    content_hash    TEXT NOT NULL           -- SHA-256 prefix, dedup key
)

-- FTS5 full-text search index
memories_fts (content)
```

## Consolidation

When the memory count exceeds `MAX_MEMORIES` (default 10,000), the hippocampus prunes the lowest-value memories — low importance, rarely accessed, oldest. This prevents unbounded growth.

The consolidation algorithm is intentionally simple: sort by importance, access count, and age, then delete the bottom. More sophisticated consolidation (merging similar memories, extracting patterns, building semantic summaries) can be layered on later without changing the schema.

## The Memory CLI

The `memory` command provides a shell interface to the hippocampus:

| Command | What it does |
|---------|-------------|
| `memory store "content"` | Store a memory (direct + stimulus) |
| `memory store -i 8 "content"` | Store with importance |
| `memory store -c food "content"` | Store with category |
| `memory search "query"` | FTS5 search, ranked by relevance |
| `memory recent [N]` | Last N memories (default 10) |
| `memory stats` | Count, categories, avg importance |

The `store` command uses a dual path: writes directly to `memory.db` (fast, same body part) AND sends a stimulus to the hippocampus (so it can process and consolidate). This means memories are immediately queryable even before the hippocampus's next cycle.

## Brain Integration (Future)

The brain organ will:
1. Read stimulus via `stimulus consume`
2. Query recent memories via `memory search` or direct SQLite access
3. Process through an LLM (Haiku, Ollama, etc.)
4. Store new memories: `memory store "I thought about X and decided Y"`
5. Send responses via `stimulus send`

The hippocampus and brain share `memory.db` on the same body part. The brain reads, the hippocampus writes and consolidates. SQLite WAL mode handles concurrent access.

## No Memory, No Problem

If the hippocampus isn't in the ORGANS list, the organism still functions — it just doesn't remember anything. Degradation, not failure. The `memory` CLI returns an error if no database exists, and the `store` command falls back to stimulus-only delivery.
