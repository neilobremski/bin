"""Tests for storage.py: store, admit_memory, dedup."""
import hashlib
import storage


def test_store_creates_memory(db):
    """store() should insert a new memory and return its id."""
    mid = storage.store(db, "This is a test memory for storage", importance=5, category="general")
    db.commit()
    assert mid is not None
    row = db.execute("SELECT content, importance, category FROM memories WHERE id=?", (mid,)).fetchone()
    assert row[0] == "This is a test memory for storage"
    assert row[1] == 5
    assert row[2] == "general"


def test_store_dedup_by_hash(db):
    """Storing identical content twice should return None the second time."""
    content = "This memory should only be stored once in the database"
    mid1 = storage.store(db, content)
    db.commit()
    mid2 = storage.store(db, content)
    db.commit()
    assert mid1 is not None
    assert mid2 is None  # duplicate


def test_store_dedup_bumps_access_count(db):
    """Storing a duplicate should increment access_count on the existing row."""
    content = "Duplicate memory that should bump access count"
    mid = storage.store(db, content, importance=5)
    db.commit()
    initial = db.execute("SELECT access_count FROM memories WHERE id=?", (mid,)).fetchone()[0]

    storage.store(db, content, importance=5)
    db.commit()
    updated = db.execute("SELECT access_count FROM memories WHERE id=?", (mid,)).fetchone()[0]
    assert updated == initial + 1


def test_store_dedup_upgrades_importance(db):
    """Storing a duplicate with higher importance should upgrade the existing row."""
    content = "Memory that gets more important over time here"
    mid = storage.store(db, content, importance=3)
    db.commit()

    storage.store(db, content, importance=8)
    db.commit()
    row = db.execute("SELECT importance FROM memories WHERE id=?", (mid,)).fetchone()
    assert row[0] == 8


def test_store_empty_content_returns_none(db):
    """store() with empty content should return None."""
    assert storage.store(db, "") is None
    assert storage.store(db, "   ") is None


def test_admit_memory_rejects_short_content():
    """admit_memory should reject content shorter than 10 characters."""
    ok, _, _ = storage.admit_memory("short")
    assert ok is False


def test_admit_memory_rejects_trivial_phrases():
    """admit_memory should reject trivial phrases like 'ok', 'sure'."""
    for phrase in ["ok", "got it", "sure", "yes", "no", "thanks"]:
        ok, _, _ = storage.admit_memory(phrase)
        assert ok is False, f"Should have rejected: {phrase}"


def test_admit_memory_accepts_valid_content():
    """admit_memory should accept meaningful content."""
    # Reset the dedup window to avoid collisions
    storage._recent_hashes.clear()
    ok, imp, cat = storage.admit_memory(
        "Neil decided to use FSRS for memory stability tracking", 5, "general"
    )
    assert ok is True


def test_admit_memory_rejects_burst_duplicates():
    """admit_memory should reject the same content within the dedup window."""
    storage._recent_hashes.clear()
    content = "This is a unique memory for burst dedup testing"
    ok1, _, _ = storage.admit_memory(content)
    assert ok1 is True
    ok2, _, _ = storage.admit_memory(content)
    assert ok2 is False  # burst duplicate


def test_admit_memory_pattern_boosts_importance():
    """admit_memory should boost importance via pattern matching (not category floors)."""
    storage._recent_hashes.clear()
    # Pattern "neil said" should set importance=8 (overrides default 5)
    ok, imp, cat = storage.admit_memory(
        "Neil said that memory is the most important thing ever", 5, "neil_insight"
    )
    assert imp == 8  # pattern match overrides default, no category floor


def test_store_sets_initial_difficulty(db):
    """store() should set difficulty = 11 - importance."""
    mid = storage.store(db, "Memory with importance eight for difficulty test", importance=8)
    db.commit()
    row = db.execute("SELECT difficulty FROM memories WHERE id=?", (mid,)).fetchone()
    assert row[0] == 3.0  # 11 - 8
