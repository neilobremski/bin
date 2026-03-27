# Hippocampus

Memory organ for the synthetic organism. Stores, retrieves, and consolidates memories using FTS5 search, FSRS stability tracking, and five-factor composite scoring.

## Quick Start

```bash
cd brain/hippocampus

# Store a memory
python3 bin/memories store "FSRS uses a power-law forgetting curve" --importance 8 --category technical

# Store another
python3 bin/memories store "Entity linking connects memories to known concepts" --importance 6

# Search
python3 bin/memories search "forgetting curve"

# Recall recent + important memories
python3 bin/memories recall --limit 5

# Stats
python3 bin/memories stats

# Create an entity and link it
python3 bin/memories entity create neil "Neil" --type person --summary "Neil Obremski, partner"
python3 bin/memories store "Neil designed the synthetic organism architecture" --importance 9

# List entities
python3 bin/memories entity list

# Entity detail (shows linked memories)
python3 bin/memories entity get neil
```

All output is JSON. The database lives at `.memory/memories.db` and is created automatically on first use.

## Organ Contract

```
hippocampus/
├── live              # Entry point (called by spark)
├── cooldown          # 0 = fire every tick
├── bin/memories      # Synchronous CLI for testing
├── src/              # Python modules
├── tests/            # pytest suite (70 tests)
├── .stimulus/        # Incoming signals (created by ganglion)
└── .memory/          # Persistent state (memories.db)
```

## Stimulus Protocol

Other organs communicate with the hippocampus by writing JSON to `.stimulus/`. Every request must include `action`, `id` (correlation), and `from` (return address).

```bash
# Store a memory
stimulus send --to hippocampus --body '{
  "action": "store",
  "content": "The deploy succeeded",
  "importance": 6,
  "category": "event",
  "id": "corr-001",
  "from": "brain"
}'

# Search
stimulus send --to hippocampus --body '{
  "action": "search",
  "query": "deploy",
  "limit": 5,
  "id": "corr-002",
  "from": "brain"
}'

# Recall (recent + important)
stimulus send --to hippocampus --body '{
  "action": "recall",
  "limit": 5,
  "id": "corr-003",
  "from": "brain"
}'

# Stats
stimulus send --to hippocampus --body '{
  "action": "stats",
  "id": "corr-004",
  "from": "brain"
}'
```

Responses are sent back via `stimulus send --to <from>` with the correlation `id` echoed. Large result sets are pushed through `circ push` with the hash in the response body.

## How It Works

**Storage:** Admission control filters trivial content, deduplicates by SHA-256 hash, and auto-supersedes near-duplicates (Jaccard >= 0.85). Explicit supersession via `"supersedes #N"` in content.

**Retrieval:** Tiered FTS5 search (hot tier first, cold tier if needed) with AND-then-OR fallback for multi-word queries. Results re-ranked by composite score:

| Factor | Weight | Source |
|--------|--------|--------|
| BM25 relevance | 0.35 | FTS5 rank |
| Importance | 0.25 | 1-10 scale |
| Recency | 0.15 | Log-decay from creation |
| FSRS retrievability | 0.15 | Power-law forgetting curve |
| UCB exploration | 0.10 | Bonus for rarely-accessed memories |

**Consolidation:** Runs every organ cycle. FSRS decay deactivates fading memories, Jaccard merge groups similar recent memories, retier reassigns hot/cold based on utility, prune caps total count.

**Entities:** Named things with aliases. Memories are auto-linked to entities when content mentions a known alias. Entity context is injected into search results.

## Tests

```bash
python3 -m pytest tests/ -v
```
