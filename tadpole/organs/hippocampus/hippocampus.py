#!/usr/bin/env python3
"""Hippocampus v2 — memory organ.

Production-quality memory system with FSRS-inspired stability tracking,
five-factor composite scoring, tiered retrieval, admission control,
auto-supersession, and category-aware consolidation.

The brain queries memory.db directly (high bandwidth, same body part).
Other organs send memories via stimulus: "remember: <content>"
Remote body parts query via nervous system (future).

Each cycle:
1. Consume stimulus — store new memories (with admission control)
2. Consolidate — FSRS decay, merge similar, retier, prune
3. Report health

This is the thin orchestrator. All logic lives in submodules:
    config.py        — constants, category config, LLM helpers
    schema.py        — init_db, migrations, backfill
    storage.py       — store, admit_memory, parse_remember, process_stimulus
    retrieval.py     — search, search_fts, composite_score, on_memory_used
    stability.py     — fsrs_retrievability, fsrs_decay, retier_memories
    consolidation.py — consolidate, find_merge_candidates, merge_memories
    supersession.py  — check_supersession, resolve_supersession, jaccard_similarity
    entities.py      — entity CRUD, extract_and_link_entities, seed_entities
"""
import os
import sqlite3

from config import DB_PATH, DIR, log
from schema import init_db, migrate, backfill_stability
from storage import consume_stimulus, process_stimulus, reset_cycle_count
from consolidation import consolidate
from entities import seed_entities
from retrieval import stats


def main():
    reset_cycle_count()

    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    init_db(db)

    # Run v2 migrations (idempotent)
    migrate(db)
    backfill_stability(db)

    # Seed entities on first run (Step 7)
    seed_entities(db)

    # Phase 1: Consume stimulus and store memories
    stimulus_text = consume_stimulus()
    stored = process_stimulus(db, stimulus_text)
    db.commit()

    # Phase 2: Consolidate
    decayed, pruned = consolidate(db)
    db.commit()

    # Phase 3: Health report
    s = stats(db)
    health = f"ok {s['active']} memories ({s['total']} total, stored {stored})"
    (DIR / "health.txt").write_text(health + "\n")
    log(f"active={s['active']} total={s['total']} stored={stored} decayed={decayed} pruned={pruned}")

    db.close()


if __name__ == "__main__":
    main()
