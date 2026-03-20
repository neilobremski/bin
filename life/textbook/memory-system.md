# Memory System

The hippocampus gives the organism memory. Without it, every spark cycle starts from zero. With it, experience accumulates: the organism remembers what happened, what mattered, and what to forget. It stores memories in a SQLite database with full-text search, deduplicates by content hash, strengthens memories that get used, and lets unused ones fade. Everything earns its place or disappears.

## The Three-Phase Cycle

Every spark cycle, the hippocampus does three things:

1. **Store** -- consume stimulus messages (`remember: ...`), run admission control, deduplicate, extract entity links, check if the new memory supersedes an old one.
2. **Consolidate** -- decay memories via FSRS forgetting curves, merge near-duplicates (LLM only), reassign hot/cold tiers, prune excess.
3. **Report** -- write `health.txt` with the active memory count.

That is the entire organ. A thin orchestrator (`hippocampus.py`, ~40 lines of logic) calls submodules for each phase.

## Storing Memories

Any organ sends a memory through stimulus:

```
stimulus send hippocampus "remember: the tadpole ate its first meal"
stimulus send hippocampus "remember important: learned to swim faster"
stimulus send hippocampus "remember critical: human fed me for the first time"
```

`remember:` = importance 5. `remember important:` = 8. `remember critical:` = 10. `remember <category>:` = custom category at importance 5. Identical content (by SHA-256 hash) bumps the access count instead of creating a duplicate.

**Admission control** rejects noise before it enters the database: content under 10 characters, trivial acks ("ok", "sure", "yes"), burst duplicates within 60 seconds, and anything past the 100-per-cycle rate limit. Pattern matching boosts importance for decisions (7) and Neil insights (8).

**Auto-supersession** replaces outdated memories. Three mechanisms: explicit reference ("supersedes memory #42"), Jaccard word similarity >= 0.85 for memories with importance < 9, and rolling windows that keep only the N most recent entries for recurring patterns like health checks.

## Schema

| Column | What it is |
|--------|-----------|
| `content` | The memory text |
| `importance` | 1-10. Higher = survives longer |
| `category` | general, decision, neil_insight, etc. |
| `is_active` | 1 = live, 0 = faded/superseded |
| `stability_days` | FSRS: days until recall drops to 90%. Grows with use. See FSRS section |
| `tier` | `hot` or `cold`. Hot tier searched first |
| `tags` | Pipe-delimited entity IDs: `\|neil\|tadpole\|` |

Other columns: `content_hash` (SHA-256 dedup key), `access_count`, `superseded_by`. Supporting tables: `entities`, `entity_memories` (links), `memories_fts` (FTS5 index, synced by triggers).

## Retrieval

Search uses FTS5 full-text matching, then re-ranks results with a five-factor score:

| Factor | Weight | What it measures |
|--------|--------|-----------------|
| **Relevance** | 0.35 | How well the query matches the memory (BM25, log-normalized) |
| **Importance** | 0.25 | The memory's importance rating, normalized to 0-1 |
| **Recency** | 0.15 | Newer memories score higher (logarithmic decay) |
| **Retrievability** | 0.15 | FSRS forgetting curve -- memories accessed often and recently score higher |
| **Exploration** | 0.10 | Bonus for rarely-accessed memories, so old gems surface |

Search hits the **hot tier** (~500 best memories) first. If that is not enough, it falls back to the **cold tier**. Multi-word queries try AND first for precision, then OR for breadth. Every memory returned by search gets an FSRS stability boost -- the act of being recalled makes it stronger.

## FSRS: Memories Get Stronger With Use

Each memory has a stability value (days until recall drops to 90%) and a difficulty value. When you search and a memory comes back, its stability grows -- and the harder the recall (low retrievability at the moment of access), the bigger the gain. Memories that are never accessed decay. When retrievability drops below 0.3 and access count is under 5, the memory is deactivated. Below 0.5, it is demoted to cold tier. No memory gets a free pass. Everything earns its place.

## Entities

Entities are named things: people, places, concepts. On first run, "Neil" and "Tadpole" are seeded. When a memory is stored, its content is scanned for known entity aliases (fast string matching, no LLM). Matches create links in `entity_memories` and populate the `tags` column. When you search, entity context (summary + top linked memories) appears below results.

## The CLI

| Command | What it does |
|---------|-------------|
| `memories store "content"` | Store (dual-write: DB + stimulus). `-i 8` for importance, `-c decision` for category |
| `memories search "query"` | FTS5 search with five-factor ranking + entity context |
| `memories recent [N]` | Last N memories (default 10) |
| `memories important [min]` | Memories with importance >= min (default 7) |
| `memories stats` | Count, tiers, categories, entity counts |
| `memories entities` | List all entities with link counts |
| `memories entity <id>` | Entity detail + linked memories |

## Optional: Small-LLM Integration

Set `HIPPOCAMPUS_USE_LLM=1` to enable: auto-importance scoring, semantic duplicate detection, and LLM-generated merge summaries during consolidation. All calls use `small-llm` with 30-second timeouts. Failures fall back silently to defaults. Off by default.

## What Was Cut (and Why)

- **Category TTLs and protection flags**: the old design had per-category TTLs and protection overrides. Removed. FSRS decay is universal -- no category gets special treatment. Memories survive by being used, not by wearing badges.
- **Hardcoded neil_insight protection**: neil_insight has no tier override or decay exemption. It has a high admission floor (importance 8 via pattern matching), which gives it natural longevity.
- **Prediction error / reconsolidation** (`last_pe_score`, `labile_until`, `recon_count`): columns exist in the schema for migration compatibility but are not read or written by any code. Reserved for future use.
- **Associations table**: created by migration, not used. Reserved for an inter-memory link graph.
- **Consolidation log**: written during merges but never read. Audit trail for future debugging.
