# Hippocampus Testing Guide

How to test and stress-test the memory organ. Use this when evaluating changes, new algorithms, or memory research implementations.

## Quick Checks

```bash
# Unit tests (64 tests, <1s)
cd tadpole/organs/hippocampus && python -m pytest tests/ -v

# Integration tests (11 tests, includes store + dedup)
cd /path/to/bin && bash tadpole/lifetime.sh
```

## Real-World Memory Test

Seed with actual memories and test retrieval quality:

```bash
export MEMORY_DB=/tmp/hippocampus-test.db
# Import from existing memory.db:
python3 -c "
import sqlite3, sys
sys.path.insert(0, 'tadpole/organs/hippocampus')
import schema, storage
src = sqlite3.connect('/path/to/memory.db')
dst = sqlite3.connect('/tmp/hippocampus-test.db')
schema.init_db(dst); schema.migrate(dst)
rows = src.execute('SELECT content, importance, category, created_at FROM memories WHERE importance >= 5 ORDER BY importance DESC LIMIT 200').fetchall()
for content, imp, cat, ts in rows:
    storage.store(dst, content, imp, cat, source='import')
dst.commit()
"
```

### Known Failure Cases (from Day 9-17)
These are real moments when memory failed. Every change should be tested against them:

| Query | Expected Result | What Failed Before |
|-------|----------------|-------------------|
| "Face TTS phone" | Phone face/TTS work from Day 8-9 | Context contamination (Day 9) |
| "executive summary" | Memory about sending exec summary | Duplicate detection (Day 14) |
| "marbles" | Neil's "don't lose your marbles" (Day 15) | Stale decay (Day 15) |
| "memory architecture" | Voice memo insights about memory | Retrieval formula imbalance |
| "phone broken offline" | Phone/phace discussions | FTS5 AND too strict (fixed: AND-then-OR) |
| "one mouth violation" | One-mouth fix details | Category-based search (removed) |

## Courtroom Stress Test

The adversarial scenario: two sides store conflicting facts, then queries probe consistency.

```bash
export MEMORY_DB=/tmp/courtroom-test.db
# See /tmp/courtroom-test-report.md for full scenario
```

### What the Courtroom Tests
1. **Consistency** — does the same fact return the same answer?
2. **Contradiction detection** — when two memories conflict, do BOTH surface?
3. **Detail accuracy** — exact numbers, names, dates retrieved correctly?
4. **Entity linking** — "What do we know about X?" surfaces all X memories
5. **Supersession** — does a correction replace the original?
6. **False memory resistance** — searching for never-stored content returns NOTHING
7. **Consolidation** — 10 near-duplicates merge into fewer without data loss

### Scoring
For each query: accuracy (right results), noise (irrelevant results), recall (missed important results), nuance (contradiction handling).

## FSRS Stability Test

Verify memories strengthen with use:

```python
# Store a memory, access it 3 times, check stability grows
mid = storage.store(db, "test memory", importance=5)
for _ in range(3):
    results = retrieval.search(db, "test memory")
row = db.execute("SELECT stability_days FROM memories WHERE id=?", (mid,)).fetchone()
assert row[0] > 1.0  # started at 1.0, should have grown
```

## Fresh-Eyes Review Protocol

After significant changes, run three independent persona reviews:

1. **Joel Spolsky** — Architecture, abstractions, simplicity
2. **John Carmack** — Performance, tight code, math correctness
3. **Steve Jobs** — Does it solve the problem? What to cut?

Each grades A-F. Track grades across iterations to verify improvement.

### Grade History
| Date | Joel | Carmack | Jobs | Notes |
|------|------|---------|------|-------|
| 2026-03-19 R1 | B- | B | C+ | Duplicate CLI scoring, Python SQL loops, dead columns |
| 2026-03-19 R2 | B | B+ | B- | Fixed scoring duplication, batch SQL. Entity system still premature per Jobs. |
| 2026-03-19 R3 | B+ | A- | B | Full CLI delegation, BM25 log transform, 10K benchmark 1.24s. BM25 unbounded. |
| 2026-03-19 R4 | A- | A- | B+ | BM25 clamped, entities slimmed 322→213, dead code cut, scripts moved. |
| 2026-03-19 R5 | A- | A- | B+ | Unified store path, N+1 CTE fix, LIMIT guard, 5-factor validated. Held. |
| 2026-03-19 R6 | B+ | B+ | B | LIMIT on LIKE, julianday pre-compute, docs. Stricter reviewer oscillated down. |

**Note on reviewer variance**: Fresh-eyes grades oscillate ±1 step because each reviewer is independently strict. The code improved consistently (R1→R6), but grades depend on reviewer harshness. Remaining issues are architectural, not patchable.

## What to Test When Adding New Features

- [ ] Does it pass all 64 unit tests?
- [ ] Does it pass lifetime.sh integration tests?
- [ ] Does it handle the 6 known failure cases correctly?
- [ ] Does the courtroom false memory test still pass (never-stored content returns nothing)?
- [ ] Does FSRS stability still increase on access?
- [ ] Is there any new duplicate code between CLI and package?
- [ ] Can it handle 10K memories without >60s cycle time?

## Cognitive Science Memory Tests (from research)

Full research at: [Drive: memory-testing-research.md](https://drive.google.com/file/d/1qEwnccod8sN_6qzNd7zv3hKe5P5Q6RRc)

### RAVLT (Rey Auditory Verbal Learning Test)
Store 15 memories, query them 5 times (learning curve), introduce interference memories, re-query (interference drop), consolidate, re-query (retention). Tests encoding, interference susceptibility, and consolidation.

### DRM False Memory Paradigm
Store semantically related memories (e.g., "bed, rest, awake, tired, dream, wake, snooze, nap") and test if the system retrieves a never-stored critical lure ("sleep"). Our AND-first strategy should prevent this.

### Proactive/Retroactive Interference
Store overlapping memory sets (Project Alpha meetings, then Project Beta meetings). Test if Beta disrupts Alpha recall (retroactive) and if Alpha disrupts Beta recall (proactive).

### Working Memory Capacity (Digit Span)
Inject N concurrent context items and test if the system can hold all of them. Find the breaking point where retrieval degrades.

### Spaced Retrieval
Store a memory, access it at increasing intervals (1 cycle, 2 cycles, 4 cycles, 8 cycles). Verify FSRS stability increases monotonically. Compare against a control memory never accessed.

### Memory Health Index
Proposed metrics for health.txt:
- recall_accuracy: what % of stored memories are retrievable?
- false_alarm_rate: what % of search results are for never-stored content?
- interference_resistance: how much does new content disrupt old recall?
- consolidation_effectiveness: do merge cycles reduce count without losing substance?
- stability_growth: does repeated access increase FSRS stability?
- learning_curve: does retrieval improve with repeated queries?
