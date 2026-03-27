"""Hippocampus organ: thin orchestrator.

Cycle: consume stimuli → process → consolidate → report.
"""
import sqlite3
import sys
from pathlib import Path

from constants import DB_PATH, DIR, log
from schema import init_db
from stimulus import consume_stimulus_files, process_stimuli
from consolidation import consolidate
from storage import reset_cycle_count


def open_db():
    """Open (or create) the memory database."""
    db_path = Path(DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(db_path))
    init_db(db)
    return db


def run_cycle():
    """One complete hippocampus cycle. Returns (stimuli_processed, decayed, pruned)."""
    db = open_db()
    try:
        reset_cycle_count()

        # Phase 1: Consume stimulus files
        stimuli = consume_stimulus_files()

        # Phase 2: Process (store/search/recall/stats)
        processed = process_stimuli(db, stimuli)

        # Phase 3: Consolidate (decay, merge, retier, prune)
        decayed, pruned = consolidate(db)

        db.commit()

        if stimuli:
            log(f"cycle: {processed}/{len(stimuli)} stimuli processed")
        if decayed or pruned:
            log(f"cycle: {decayed} decayed, {pruned} pruned")

        return processed, decayed, pruned
    except Exception as e:
        log(f"cycle error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def main():
    try:
        processed, decayed, pruned = run_cycle()
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
