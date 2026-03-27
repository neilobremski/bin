"""Tests for stability.py: fsrs_retrievability, fsrs_decay, retier_memories."""
import hashlib
from datetime import datetime, timezone, timedelta

import stability
from constants import TS_FMT


def test_fsrs_retrievability_at_zero_elapsed():
    now = datetime.now(timezone.utc)
    accessed = now.strftime(TS_FMT)
    r = stability.fsrs_retrievability(accessed, stability_days=10.0, now=now)
    assert abs(r - 1.0) < 0.01


def test_fsrs_retrievability_at_stability():
    now = datetime.now(timezone.utc)
    S = 10.0
    accessed = (now - timedelta(days=S)).strftime(TS_FMT)
    r = stability.fsrs_retrievability(accessed, stability_days=S, now=now)
    assert abs(r - 0.9) < 0.01


def test_fsrs_retrievability_at_nine_times_stability():
    now = datetime.now(timezone.utc)
    S = 5.0
    accessed = (now - timedelta(days=9 * S)).strftime(TS_FMT)
    r = stability.fsrs_retrievability(accessed, stability_days=S, now=now)
    assert abs(r - 0.5) < 0.01


def test_fsrs_retrievability_decreases_over_time():
    now = datetime.now(timezone.utc)
    S = 10.0
    r1 = stability.fsrs_retrievability(
        (now - timedelta(days=1)).strftime(TS_FMT), S, now)
    r2 = stability.fsrs_retrievability(
        (now - timedelta(days=30)).strftime(TS_FMT), S, now)
    assert r1 > r2


def _insert_memory(db, content, importance=5, category="general",
                    access_count=0, days_ago=0, stability_days=1.0):
    now = datetime.now(timezone.utc)
    created = (now - timedelta(days=days_ago)).strftime(TS_FMT)
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
    mid = _insert_memory(db, "Old observation that should decay away from memory",
                         category="observation", days_ago=60, stability_days=0.5,
                         access_count=1)
    now = datetime.now(timezone.utc)
    decayed = stability.fsrs_decay(db, now)
    assert decayed >= 1
    row = db.execute("SELECT is_active FROM memories WHERE id=?", (mid,)).fetchone()
    assert row[0] == 0


def test_fsrs_decay_preserves_high_access_memories(db):
    mid = _insert_memory(db, "Well-accessed observation that should survive decay",
                         category="observation", days_ago=60, stability_days=0.5,
                         access_count=10)
    now = datetime.now(timezone.utc)
    stability.fsrs_decay(db, now)
    row = db.execute("SELECT is_active FROM memories WHERE id=?", (mid,)).fetchone()
    assert row[0] == 1


def test_fsrs_decay_preserves_recent_memories(db):
    mid = _insert_memory(db, "Recent observation that is still fresh in memory",
                         category="observation", days_ago=1, stability_days=10.0,
                         access_count=0)
    now = datetime.now(timezone.utc)
    stability.fsrs_decay(db, now)
    row = db.execute("SELECT is_active FROM memories WHERE id=?", (mid,)).fetchone()
    assert row[0] == 1
