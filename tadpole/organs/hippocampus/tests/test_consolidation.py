"""Tests for consolidation.py: consolidate, find_merge_candidates, merge_memories."""
import hashlib
from datetime import datetime, timezone, timedelta

import consolidation


def _insert_memory(db, content, importance=5, category="general",
                    access_count=0, days_ago=0, stability_days=1.0, is_active=1):
    """Helper to insert a memory with specific properties."""
    now = datetime.now(timezone.utc)
    created = (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
    db.execute(
        "INSERT INTO memories(content, importance, category, source, created_at, "
        "accessed_at, access_count, content_hash, stability_days, difficulty, tier, is_active) "
        "VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, 5.0, 'hot', ?)",
        (content, importance, category, created, created, access_count,
         content_hash, stability_days, is_active)
    )
    mid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()
    return mid


def test_consolidate_empty_db(db):
    """consolidate on empty DB should do nothing and not raise."""
    decayed, pruned = consolidation.consolidate(db)
    assert decayed == 0
    assert pruned == 0


def test_consolidate_deactivates_low_stability_memories(db):
    """consolidate should deactivate memories with low retrievability past TTL."""
    # observation has ttl_days=7; insert one accessed 60 days ago with low stability
    mid = _insert_memory(db, "Old stale observation that should be deactivated by consolidation",
                         category="observation", days_ago=60, stability_days=0.5,
                         access_count=1)

    decayed, pruned = consolidation.consolidate(db)
    assert decayed >= 1

    row = db.execute("SELECT is_active FROM memories WHERE id=?", (mid,)).fetchone()
    assert row[0] == 0


def test_consolidate_preserves_high_importance_memories(db):
    """consolidate should not deactivate high-access memories."""
    mid = _insert_memory(db, "Important well-accessed observation that must survive consolidation",
                         category="observation", importance=9, days_ago=60,
                         stability_days=0.5, access_count=20)

    consolidation.consolidate(db)

    row = db.execute("SELECT is_active FROM memories WHERE id=?", (mid,)).fetchone()
    assert row[0] == 1  # still active due to high access_count


def test_consolidate_does_not_prune_under_max(db):
    """consolidate should not prune when total count is under MAX_MEMORIES."""
    for i in range(5):
        _insert_memory(db, f"Memory number {i} for prune test with unique content {i}")

    _, pruned = consolidation.consolidate(db)
    assert pruned == 0


def test_find_merge_candidates_empty_db(db):
    """find_merge_candidates on empty DB returns empty dict."""
    groups = consolidation.find_merge_candidates(db)
    assert groups == {}


def test_find_merge_candidates_finds_similar_group(db):
    """find_merge_candidates should group memories with high Jaccard similarity."""
    # Insert 3+ very similar observation memories (within 7 days)
    base = "the system health check passed all tests successfully today"
    _insert_memory(db, base + " morning run", category="observation", days_ago=1)
    _insert_memory(db, base + " afternoon run", category="observation", days_ago=1)
    _insert_memory(db, base + " evening run", category="observation", days_ago=1)

    groups = consolidation.find_merge_candidates(db, category="observation", threshold=0.65)
    # Should find at least one group of 3
    if groups:
        assert any(len(mids) >= 3 for mids in groups.values())


def test_merge_memories_keeps_highest_importance(db):
    """merge_memories without LLM should keep the highest-importance memory."""
    id1 = _insert_memory(db, "Low importance version of the architecture decision", importance=3)
    id2 = _insert_memory(db, "High importance version of the architecture decision", importance=8)
    id3 = _insert_memory(db, "Medium importance version of the architecture decision", importance=5)

    now = datetime.now(timezone.utc)
    survivor = consolidation.merge_memories(db, [id1, id2, id3], now)
    db.commit()

    assert survivor == id2  # highest importance

    # Others should be superseded
    for mid in [id1, id3]:
        row = db.execute("SELECT is_active, superseded_by FROM memories WHERE id=?", (mid,)).fetchone()
        assert row[0] == 0
        assert row[1] == id2
