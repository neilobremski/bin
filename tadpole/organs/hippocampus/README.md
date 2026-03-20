# Hippocampus — Memory Organ

Stores, retrieves, and consolidates memories. The brain queries memory.db directly (same body part, high bandwidth). Other organs send memories via `stimulus send hippocampus "remember: ..."`.

## Modules

| Module | Lines | Purpose |
|--------|-------|---------|
| `hippocampus.py` | ~73 | Thin orchestrator — runs the 3-phase cycle |
| `config.py` | ~180 | Constants, LLM helpers, admission rules |
| `schema.py` | ~154 | Tables, migrations, backfill |
| `storage.py` | ~204 | Store, admit, dedup, stimulus processing |
| `retrieval.py` | ~254 | Search (FTS5 + 5-factor scoring), tiered access |
| `stability.py` | ~97 | FSRS on-access updates, batch decay, retiering |
| `consolidation.py` | ~161 | Merge similar memories, prune inactive |
| `supersession.py` | ~88 | Jaccard similarity, chain resolution |
| `entities.py` | ~250 | Entity CRUD, extraction, linking, unconscious recall |

## How It Works

Each spark cycle:
1. **Store** — consume stimulus, admit valid memories, extract entities, check supersession
2. **Consolidate** — FSRS decay, merge near-duplicates, retier hot/cold
3. **Report** — write health.txt with memory count

## Key Design Principles

- **Emergence over prescription** — memories survive by being used, not by wearing category badges
- **Precision over recall** — AND-first search prevents false memories; OR fallback catches partial matches
- **FSRS stability** — memories strengthen with use, weaken without (no hardcoded TTLs)
- **Entity connections** — memories link to entities (people, things, concepts) via tags + junction table

## CLI

```bash
memories store "content"          # store a memory
memories search "query"           # FTS5 + 5-factor scoring
memories recent 10                # last 10 memories
memories important 8              # importance >= 8
memories entities                 # list all entities
memories entity neil              # entity detail + linked memories
memories stats                    # count, avg importance, tiers
```

## Testing

See [testing.md](testing.md) for the full testing guide including:
- 64 pytest unit tests
- 6 known failure cases from real memory failures
- Courtroom adversarial stress test
- FSRS stability verification
- Fresh-eyes review protocol (Joel/Carmack/Jobs personas)
