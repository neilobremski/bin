"""Tests for stability.py: fsrs_retrievability, fsrs_decay, retier_memories."""
import math
from datetime import datetime, timezone, timedelta

import stability


def test_fsrs_retrievability_at_zero_elapsed():
    """At t=0, retrievability should be 1.0."""
    now = datetime.now(timezone.utc)
    accessed = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    r = stability.fsrs_retrievability(accessed, stability_days=10.0, now=now)
    assert abs(r - 1.0) < 0.01


def test_fsrs_retrievability_at_stability():
    """At t=S, retrievability should be approximately 0.9 (by FSRS formula)."""
    now = datetime.now(timezone.utc)
    S = 10.0
    accessed = (now - timedelta(days=S)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = stability.fsrs_retrievability(accessed, stability_days=S, now=now)
    # R(S) = 1 / (1 + S/(9*S)) = 1 / (1 + 1/9) = 9/10 = 0.9
    assert abs(r - 0.9) < 0.01


def test_fsrs_retrievability_at_nine_times_stability():
    """At t=9*S, retrievability should be approximately 0.5."""
    now = datetime.now(timezone.utc)
    S = 5.0
    accessed = (now - timedelta(days=9 * S)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = stability.fsrs_retrievability(accessed, stability_days=S, now=now)
    # R(9S) = 1 / (1 + 9S/(9*S)) = 1/2 = 0.5
    assert abs(r - 0.5) < 0.01


def test_fsrs_retrievability_decreases_over_time():
    """Retrievability should decrease as time since access increases."""
    now = datetime.now(timezone.utc)
    S = 10.0
    r1 = stability.fsrs_retrievability(
        (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), S, now
    )
    r2 = stability.fsrs_retrievability(
        (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"), S, now
    )
    assert r1 > r2


def _insert_memory(db, content, importance=5, category="general",
                    access_count=0, days_ago=0, stability_days=1.0):
    """Helper to insert a memory with specific properties."""
    import hashlib
    now = datetime.now(timezone.utc)
    created = (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
    db.execute(
        "INSERT INTO memories(content, importance, category, source, created_at, "
        "accessed_at, access_count, content_hash, stability_days, difficulty, tier, is_active) "
        "VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, 5.0, 'hot', 1)",
        (content, importance, category, created, created, access_count,
         content_hash, stability_days)
    )
    mid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()
    return mid


def test_fsrs_decay_deactivates_low_retrievability_memories(db):
    """fsrs_decay should deactivate memories with R < 0.3 that are past TTL."""
    # observation has ttl_days=7. Insert one accessed 60 days ago with low stability.
    mid = _insert_memory(db, "Old observation that should decay away from memory",
                         category="observation", days_ago=60, stability_days=0.5,
                         access_count=1)
    now = datetime.now(timezone.utc)
    decayed = stability.fsrs_decay(db, now)
    assert decayed >= 1

    row = db.execute("SELECT is_active FROM memories WHERE id=?", (mid,)).fetchone()
    assert row[0] == 0  # deactivated


def test_fsrs_decay_preserves_high_access_memories(db):
    """fsrs_decay should NOT deactivate memories with access_count >= 5."""
    mid = _insert_memory(db, "Well-accessed observation that should survive decay",
                         category="observation", days_ago=60, stability_days=0.5,
                         access_count=10)
    now = datetime.now(timezone.utc)
    stability.fsrs_decay(db, now)

    row = db.execute("SELECT is_active FROM memories WHERE id=?", (mid,)).fetchone()
    assert row[0] == 1  # still active


def test_fsrs_decay_preserves_recent_memories(db):
    """fsrs_decay should NOT deactivate recently accessed memories."""
    mid = _insert_memory(db, "Recent observation that is still fresh in memory",
                         category="observation", days_ago=1, stability_days=10.0,
                         access_count=0)
    now = datetime.now(timezone.utc)
    stability.fsrs_decay(db, now)

    row = db.execute("SELECT is_active FROM memories WHERE id=?", (mid,)).fetchone()
    assert row[0] == 1
