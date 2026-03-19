# Memory System

The memory system gives an organism the ability to accumulate experience across spark cycles. Without memory, every cycle starts from zero -- the organism learns nothing.

## The Hippocampus

The hippocampus is a memory organ. It stores memories in SQLite with full-text search (FTS5), deduplicates them by content hash, tracks stability via FSRS-inspired forgetting curves, and consolidates over time.

The hippocampus runs on cadence like any organ. Each cycle:

1. **Consume** -- read stimulus for `remember:` commands, apply admission control, store as memories
2. **Consolidate** -- FSRS decay, merge similar memories, reassign tiers, prune excess
3. **Report** -- write health status (total memory count, tier breakdown)

## Storing Memories

Any organ can send a memory through the nervous system:

```bash
stimulus send hippocampus "remember: the tadpole ate its first meal"
stimulus send hippocampus "remember important: learned to swim faster"
stimulus send hippocampus "remember critical: human fed me for the first time"
```

Importance levels:
- `remember:` -- importance 5 (default)
- `remember important:` -- importance 8
- `remember critical:` -- importance 10
- `remember <category>:` -- custom category, importance 5

Each memory is deduplicated by SHA-256 content hash. Storing the same content twice updates the access timestamp instead of creating a duplicate.

### Admission Control

Before storing, memories pass through admission control:

1. **Minimum length**: content shorter than 10 characters is rejected as noise
2. **Pattern matching**: trivial acks ("ok", "sure", "yes") are rejected; decisions and neil insights get importance boosts
3. **Dedup window**: identical content within 60 seconds is rejected (burst dedup)
4. **Rate limit**: max 100 memories per cycle
5. **Category floors**: each category has a minimum importance (e.g. neil_insight >= 7)

### Auto-Supersession

When a new memory is stored, the hippocampus checks if it supersedes an existing one:

1. **Explicit reference**: content containing "supersedes memory #NNN" marks the old memory inactive
2. **Jaccard similarity**: for decision/general categories (importance < 9), if a new memory has >= 0.85 word-level Jaccard similarity with an existing one, the old one is superseded
3. **Rolling windows**: recurring patterns (session_reflection, health_check, morning_ritual, evening_ritual) keep only the N most recent entries

## Active and Inactive Memories

Every memory has an `is_active` flag. Active memories (is_active=1) are the working set -- they appear in searches, recent lists, and stats. Inactive memories (is_active=0) are retained in the database but excluded from queries.

Memories become inactive through three paths:
- **FSRS Decay**: when retrievability drops below 0.3 and the memory is past its category TTL with few accesses
- **Supersession**: when a newer memory replaces an older one
- **Manual deactivation**: explicit deactivation by the brain

Inactive memories are not deleted immediately. They persist as a low-priority archive until the database exceeds MAX_MEMORIES, at which point the lowest-value inactive memories are pruned.

## Recalling Memories

The brain (or any organ on the same body part) reads `memory.db` directly. This is the high-bandwidth path -- no network, no stimulus delay, just SQLite on the local filesystem.

```bash
# CLI interface
memories search "food"        # FTS5 full-text search with smart ranking
memories recent 5             # last 5 memories
memories important 8          # memories with importance >= 8
memories stats                # count, categories, avg importance, tier breakdown
```

## Smart Retrieval (v2)

Search results are ranked using a five-factor composite score:

```
score = 0.35 * relevance + 0.25 * importance + 0.15 * recency
      + 0.15 * retrievability + 0.10 * exploration
```

- **Relevance** (weight 0.35): BM25 score from FTS5, sigmoid-normalized. Measures query-memory match.
- **Importance** (weight 0.25): The memory's importance rating (1-10), normalized to 0-1.
- **Recency** (weight 0.15): `1 / (1 + log(1 + age_days))`. Recent memories get a logarithmic boost.
- **Retrievability** (weight 0.15): FSRS v4 forgetting curve `R(t,S) = (1 + t/(9*S))^(-1)`. Memories with high stability (frequently accessed, confirmed relevant) score higher.
- **Exploration** (weight 0.10): UCB-inspired bonus `sqrt(2*log(N+1)/(n+1))`. Prevents "rich get richer" -- rarely accessed but relevant memories get a boost.

### Tiered Retrieval

Memories are split into hot and cold tiers:
- **Hot tier** (~500 memories): searched first. Contains the most useful memories by utility score.
- **Cold tier**: searched only when hot tier yields fewer than the requested limit.

Tier assignment happens during consolidation based on `importance * retrievability * (1 + log(access_count + 1))`. Category overrides apply (neil_insight always hot).

## FSRS Stability Tracking

Each memory tracks two FSRS-inspired values:
- **stability_days**: how many days until retrievability drops to 90%. Starts at 1.0, grows on relevant access.
- **difficulty**: how hard this memory is to retrieve (1-10). Starts at `11 - importance`.

When a memory is accessed during search:
- If relevant: stability grows (desirable difficulty effect -- harder recalls strengthen more)
- If irrelevant: stability decays by 10%, difficulty increases

The FSRS formulas are simplified from FSRS v4 since we cannot ML-fit parameters:
```
Retrievability: R(t, S) = (1 + t/(9*S))^(-1)
Stability gain: S_new = S * (1 + 0.1 + 0.3 * difficulty_factor * retrievability_bonus)
Stability loss: S_new = S * 0.9
```

## Schema (v2)

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
    is_active       INTEGER DEFAULT 1,      -- 1=active, 0=inactive
    -- v2 columns:
    stability_days  REAL DEFAULT 1.0,       -- FSRS stability (days to 90% recall)
    difficulty      REAL DEFAULT 5.0,       -- FSRS difficulty (1-10)
    last_pe_score   REAL DEFAULT 0.0,       -- prediction error score
    labile_until    TEXT DEFAULT NULL,       -- reconsolidation window end
    recon_count     INTEGER DEFAULT 0,      -- reconsolidation count
    tier            TEXT DEFAULT 'hot',     -- 'hot' or 'cold'
    tags            TEXT DEFAULT ''         -- pipe-delimited entity tags: |neil|tadpole|
)

-- Supporting tables:
entities (id, name, aliases, entity_type, summary, properties, created_at, updated_at)
entity_memories (entity_id, memory_id, relationship, valid_from, valid_until)
associations (source_id, target_id, link_type, strength, created_at)
consolidation_log (id, operation, source_ids, result_id, summary, verified, created_at)
schema_migrations (id, applied_at)

-- FTS5 full-text search index (kept in sync by triggers)
memories_fts (content)
```

## Entity System

Entities are named things the organism knows about: people, places, concepts. Each entity has a name, aliases (alternate names), a type, and a summary. Entities live in the `entities` table.

### How Entities Work

1. **Seeding**: On first run, the hippocampus seeds initial entities (Neil, Tadpole). These are bootstrapped from `SEED_ENTITIES` in hippocampus.py.

2. **Entity extraction on store**: When a new memory is stored, the hippocampus scans its content for known entity aliases (fast string matching, no LLM). Matches create links in the `entity_memories` junction table and populate the `tags` column on the memory.

3. **Tags**: Each memory has a `tags` column with pipe-delimited entity IDs: `|neil|tadpole|`. This enables fast display and LIKE-based filtering (`WHERE tags LIKE '%|neil|%'`). The junction table (`entity_memories`) provides the relational path for proper queries.

4. **Entity-aware search**: When search results reference entities, the CLI shows entity context (summary + top linked memories) below the results.

5. **Unconscious entity recall**: When building context for the brain, the hippocampus scans the input message for entity mentions and auto-injects entity summaries and linked memories. This is the unconscious path -- no explicit search needed.

6. **Entity summary evolution**: When `HIPPOCAMPUS_USE_LLM=1`, entity summaries are regenerated from linked memories during consolidation.

### Entity CLI

```bash
memories entities              # list all entities with link counts
memories entity neil           # show entity detail + linked memories
```

### Design Decisions

- Entity extraction is fast (string matching, no LLM) -- runs on every store
- Tags column is additive (pipe-delimited with bookends) for fast display
- Junction table provides the relational path for proper queries
- neil_insight memories do NOT get special protection in tiering/decay -- they survive by being relevant and accessed, not by having a badge

## Category Configuration

| Category | TTL (days) | Min Importance | Protected | Tier Override |
|----------|-----------|----------------|-----------|---------------|
| neil_insight | never | 7 | no | -- |
| decision | 30 | 5 | no | -- |
| observation | 7 | 1 | no | -- |
| research | 14 | 3 | no | -- |
| system | 1 | 1 | no | -- |
| general | 14 | 1 | no | -- |

No categories receive special protection. neil_insight memories have no TTL expiry and a high minimum importance floor (7), which gives them natural longevity -- but they must earn their place through relevance and access like everything else.

## Consolidation (v2)

Consolidation runs every hippocampus cycle in three phases:

1. **FSRS Decay** -- for each active, unprotected memory, compute retrievability. If R < 0.3 and past category TTL with few accesses: deactivate. If R < 0.5 and past TTL: demote to cold tier.

2. **Merge** (LLM only) -- within each category, find groups of 3+ memories with Jaccard similarity >= 0.65. Merge them into a single memory (LLM summarizes; without LLM, keep highest-importance and supersede rest). Max 5 merges per cycle.

3. **Tier & Prune** -- reassign all active memories to hot/cold based on utility score. Prune excess inactive memories if over MAX_MEMORIES.

## The Memory CLI

The `memories` command is a Python CLI that provides a shell interface to the hippocampus. It uses parameterized SQL queries throughout, WAL mode for concurrent access, and reads `MEMORY_DB` from the environment (default: `$PWD/organs/hippocampus/memory.db`).

| Command | What it does |
|---------|-------------|
| `memories store "content"` | Store a memory (direct + stimulus) |
| `memories store -i 8 "content"` | Store with explicit importance |
| `memories store -c food "content"` | Store with category |
| `memories search "query"` | FTS5 search, ranked by v2 five-factor scoring, with entity context |
| `memories recent [N]` | Last N memories (default 10) |
| `memories important [min]` | Memories with importance >= min (default 7) |
| `memories stats` | Count, categories, avg importance, tier breakdown, entity counts |
| `memories entities` | List all entities with link counts |
| `memories entity <id>` | Show entity detail + linked memories |

The `store` command uses a dual path: writes directly to `memory.db` (fast, same body part) AND sends a stimulus to the hippocampus (so it can process and consolidate).

## Small-LLM Integration (Optional)

When `HIPPOCAMPUS_USE_LLM=1` is set, the hippocampus gains LLM-powered capabilities:
- **Auto-importance scoring**: memories without explicit importance get LLM-rated scores
- **Similarity detection**: semantic duplicate detection beyond hash matching
- **Merge summaries**: consolidation merges generate LLM summaries

All LLM calls use the `small-llm` CLI with 30-second timeouts. Failures fall back silently to default behavior.

## Migration

Schema migrations are tracked in the `schema_migrations` table. The `migrate()` function runs on every startup and is fully idempotent -- safe to run on both fresh and existing databases. Existing memories get backfilled with initial stability_days and difficulty values based on their access history.
