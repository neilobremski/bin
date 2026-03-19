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

## Active and Inactive Memories

Every memory has an `is_active` flag. Active memories (is_active=1) are the working set — they appear in searches, recent lists, and stats. Inactive memories (is_active=0) are retained in the database but excluded from queries.

Memories become inactive through two paths:
- **Decay**: stale, low-importance, rarely accessed memories are marked inactive during consolidation (importance < 5, access_count < 2, older than STALE_DAYS)
- **Supersession**: when a newer memory replaces an older one, the old memory's `superseded_by` field points to its replacement and it becomes inactive

Inactive memories are not deleted immediately. They persist as a low-priority archive until the database exceeds MAX_MEMORIES, at which point the lowest-value inactive memories are pruned.

## Recalling Memories

The brain (or any organ on the same body part) reads `memory.db` directly. This is the high-bandwidth path — no network, no stimulus delay, just SQLite on the local filesystem.

```bash
# CLI interface
memories search "food"        # FTS5 full-text search with smart ranking
memories recent 5             # last 5 memories
memories important 8          # memories with importance >= 8
memories stats                # count, categories, avg importance
```

For remote body parts (future), the pattern is: send a query stimulus to the hippocampus, include your organ type so the hippocampus can send the results back. This is the low-bandwidth path — eventually consistent, one cycle of latency.

## Smart Retrieval

Search results are not returned in raw FTS5 order. The hippocampus re-ranks them using a composite score that balances three factors:

```
score = 0.4 * relevance + 0.35 * importance + 0.25 * recency
```

- **Relevance** (weight 0.4): BM25 score from FTS5 full-text search. Measures how well the memory matches the query terms.
- **Importance** (weight 0.35): The memory's importance rating (1-10), normalized to 0-1. Critical memories float to the top even if they are older or less textually relevant.
- **Recency** (weight 0.25): Computed as `1 / (1 + log(1 + age_days))`. Recent memories get a boost that decays logarithmically — a one-day-old memory scores much higher than a 30-day-old one, but the difference between 30 and 60 days is small.

The search over-fetches 3x the requested limit from FTS5, re-ranks with the composite score, then returns the top N. This ensures that a highly important but textually marginal memory can still surface.

## Schema

```sql
memories (
    id              INTEGER PRIMARY KEY,
    content         TEXT NOT NULL,
    importance      INTEGER DEFAULT 5,     -- 1-10
    category        TEXT DEFAULT 'general',
    source          TEXT DEFAULT '',        -- origin: 'cli', 'stimulus', etc.
    created_at      TEXT NOT NULL,
    accessed_at     TEXT NOT NULL,
    access_count    INTEGER DEFAULT 0,
    content_hash    TEXT NOT NULL UNIQUE,   -- SHA-256 prefix, dedup key
    superseded_by   INTEGER DEFAULT NULL,   -- id of replacement memory
    is_active       INTEGER DEFAULT 1       -- 1=active, 0=inactive
)

-- FTS5 full-text search index (kept in sync by triggers)
memories_fts (content)
```

## Consolidation

Consolidation runs every hippocampus cycle in two phases:

1. **Decay** — mark stale low-value memories as inactive (importance < 5, access_count < 2, older than STALE_DAYS). These memories are not deleted; they just stop appearing in queries.

2. **Prune** — if total memory count exceeds MAX_MEMORIES (default 10,000), hard-delete the lowest-value inactive memories (sorted by importance ASC, access_count ASC, created_at ASC).

This two-phase approach means memories degrade gracefully: they go quiet before they disappear.

## The Memory CLI

The `memories` command is a Python CLI that provides a shell interface to the hippocampus. It uses parameterized SQL queries throughout, WAL mode for concurrent access, and reads `MEMORY_DB` from the environment (default: `$PWD/organs/hippocampus/memory.db`).

| Command | What it does |
|---------|-------------|
| `memories store "content"` | Store a memory (direct + stimulus) |
| `memories store -i 8 "content"` | Store with explicit importance |
| `memories store -c food "content"` | Store with category |
| `memories search "query"` | FTS5 search, ranked by smart retrieval |
| `memories recent [N]` | Last N memories (default 10) |
| `memories important [min]` | Memories with importance >= min (default 7) |
| `memories stats` | Count, categories, avg importance |

The `store` command uses a dual path: writes directly to `memory.db` (fast, same body part) AND sends a stimulus to the hippocampus (so it can process and consolidate). This means memories are immediately queryable even before the hippocampus's next cycle.

## Small-LLM Integration (Optional)

When `HIPPOCAMPUS_USE_LLM=1` is set, the hippocampus gains two LLM-powered capabilities via the `small-llm` CLI:

**Auto-importance scoring**: When a memory arrives without explicit importance (default 5), the LLM rates it 1-10. This means memories stored via plain `remember:` stimulus get smarter importance than the flat default.

**Similarity detection**: Before storing a new memory, the hippocampus asks the LLM to compare it against the 20 most recent memories. If the LLM identifies a semantic duplicate (same meaning, different words), the existing memory's access count is bumped instead of creating a new entry. This catches duplicates that hash-based dedup misses.

Both features are off by default. When `HIPPOCAMPUS_USE_LLM` is unset or not `1`, the hippocampus works purely with SQLite — fast, predictable, no external dependencies. The LLM integration calls `small-llm` via subprocess with a 30-second timeout; failures fall back silently to default behavior.

```bash
# Run hippocampus with LLM features
HIPPOCAMPUS_USE_LLM=1 python3 hippocampus.py

# Run without (default, pure SQLite)
python3 hippocampus.py
```

## Brain Integration (Future)

The brain organ will:
1. Read stimulus via `stimulus consume`
2. Query recent memories via `memories search` or direct SQLite access
3. Process through an LLM (Haiku, Ollama, etc.)
4. Store new memories: `memories store "I thought about X and decided Y"`
5. Send responses via `stimulus send`

The hippocampus and brain share `memory.db` on the same body part. The brain reads, the hippocampus writes and consolidates. SQLite WAL mode handles concurrent access.

## No Memory, No Problem

If the hippocampus isn't in the ORGANS list, the organism still functions — it just doesn't remember anything. Degradation, not failure. The `memories` CLI returns an error if no database exists, and the `store` command falls back to stimulus-only delivery.
