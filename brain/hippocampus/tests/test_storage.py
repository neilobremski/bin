"""Tests for storage.py: store, admit_memory, dedup."""
import storage


def test_store_creates_memory(db):
    mid = storage.store(db, "This is a test memory for storage", importance=5, category="general")
    db.commit()
    assert mid is not None
    row = db.execute("SELECT content, importance, category FROM memories WHERE id=?", (mid,)).fetchone()
    assert row[0] == "This is a test memory for storage"
    assert row[1] == 5
    assert row[2] == "general"


def test_store_dedup_by_hash(db):
    content = "This memory should only be stored once in the database"
    mid1 = storage.store(db, content)
    db.commit()
    mid2 = storage.store(db, content)
    db.commit()
    assert mid1 is not None
    assert mid2 is None


def test_store_dedup_bumps_access_count(db):
    content = "Duplicate memory that should bump access count"
    mid = storage.store(db, content, importance=5)
    db.commit()
    initial = db.execute("SELECT access_count FROM memories WHERE id=?", (mid,)).fetchone()[0]
    storage.store(db, content, importance=5)
    db.commit()
    updated = db.execute("SELECT access_count FROM memories WHERE id=?", (mid,)).fetchone()[0]
    assert updated == initial + 1


def test_store_dedup_upgrades_importance(db):
    content = "Memory that gets more important over time here"
    mid = storage.store(db, content, importance=3)
    db.commit()
    storage.store(db, content, importance=8)
    db.commit()
    row = db.execute("SELECT importance FROM memories WHERE id=?", (mid,)).fetchone()
    assert row[0] == 8


def test_store_empty_content_returns_none(db):
    assert storage.store(db, "") is None
    assert storage.store(db, "   ") is None


def test_admit_memory_rejects_short_content():
    ok, _, _ = storage.admit_memory("short")
    assert ok is False


def test_admit_memory_rejects_trivial_phrases():
    for phrase in ["ok", "got it", "sure", "yes", "no", "thanks"]:
        ok, _, _ = storage.admit_memory(phrase)
        assert ok is False, f"Should have rejected: {phrase}"


def test_admit_memory_accepts_valid_content():
    storage._recent_hashes.clear()
    ok, imp, cat = storage.admit_memory(
        "Neil decided to use FSRS for memory stability tracking", 5, "general"
    )
    assert ok is True


def test_admit_memory_rejects_burst_duplicates():
    storage._recent_hashes.clear()
    content = "This is a unique memory for burst dedup testing"
    ok1, _, _ = storage.admit_memory(content)
    assert ok1 is True
    ok2, _, _ = storage.admit_memory(content)
    assert ok2 is False


def test_store_sets_initial_difficulty(db):
    mid = storage.store(db, "Memory with importance eight for difficulty test", importance=8)
    db.commit()
    row = db.execute("SELECT difficulty FROM memories WHERE id=?", (mid,)).fetchone()
    assert row[0] == 3.0  # 11 - 8
