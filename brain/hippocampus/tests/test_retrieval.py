"""Tests for retrieval.py: search, composite_score, on_memory_used."""
import hashlib
import math
from datetime import datetime, timezone

import retrieval
from constants import TS_FMT


def _insert_memory(db, content, importance=5, category="general"):
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
    now = datetime.now(timezone.utc).strftime(TS_FMT)
    db.execute(
        "INSERT INTO memories(content, importance, category, source, created_at, "
        "accessed_at, access_count, content_hash, stability_days, difficulty, tier) "
        "VALUES (?, ?, ?, '', ?, ?, 0, ?, 1.0, 5.0, 'hot')",
        (content, importance, category, now, now, content_hash)
    )
    mid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()
    return mid


def test_search_returns_stored_memories(db):
    _insert_memory(db, "The FSRS algorithm tracks memory stability over time", importance=7)
    _insert_memory(db, "Jaccard similarity compares word overlap between texts", importance=5)
    results = retrieval.search(db, "FSRS stability")
    assert len(results) >= 1
    contents = [r[1] for r in results]
    assert any("FSRS" in c for c in contents)


def test_search_no_results_returns_empty(db):
    _insert_memory(db, "The quick brown fox jumps over the lazy dog", importance=5)
    results = retrieval.search(db, "quantum entanglement superposition")
    assert results == []


def test_search_empty_query_returns_empty(db):
    assert retrieval.search(db, "") == []
    assert retrieval.search(db, "   ") == []


def test_composite_score_weights_sum_to_one():
    total = 0.35 + 0.25 + 0.15 + 0.15 + 0.10
    assert abs(total - 1.0) < 1e-9


def test_composite_score_returns_between_zero_and_one():
    score = retrieval.composite_score(
        bm25_score=-5.0, importance=7, age_days=1.0,
        access_count=3, stability_days=10.0, total_queries=100,
    )
    assert 0.0 <= score <= 1.0


def test_composite_score_higher_importance_scores_higher():
    kwargs = dict(bm25_score=-3.0, age_days=1.0, access_count=2,
                  stability_days=5.0, total_queries=50)
    score_low = retrieval.composite_score(importance=2, **kwargs)
    score_high = retrieval.composite_score(importance=9, **kwargs)
    assert score_high > score_low


def test_on_memory_used_increases_stability_when_relevant(db):
    mid = _insert_memory(db, "Memory that will be accessed as relevant content", importance=7)
    before = db.execute("SELECT stability_days FROM memories WHERE id=?", (mid,)).fetchone()[0]
    retrieval.on_memory_used(db, mid, was_relevant=True)
    db.commit()
    after = db.execute("SELECT stability_days FROM memories WHERE id=?", (mid,)).fetchone()[0]
    assert after > before


def test_on_memory_used_decreases_stability_when_irrelevant(db):
    mid = _insert_memory(db, "Memory that will be accessed as irrelevant content", importance=5)
    before = db.execute("SELECT stability_days FROM memories WHERE id=?", (mid,)).fetchone()[0]
    retrieval.on_memory_used(db, mid, was_relevant=False)
    db.commit()
    after = db.execute("SELECT stability_days FROM memories WHERE id=?", (mid,)).fetchone()[0]
    assert after < before


def test_on_memory_used_nonexistent_id(db):
    retrieval.on_memory_used(db, 99999, was_relevant=True)
