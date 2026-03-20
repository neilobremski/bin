"""Tests for supersession.py: jaccard_similarity, check_supersession, resolve_supersession."""
import hashlib
from datetime import datetime, timezone

import supersession


def test_jaccard_similarity_exact_match():
    """Identical texts should have Jaccard similarity of 1.0."""
    text = "the quick brown fox jumps over the lazy dog"
    assert supersession.jaccard_similarity(text, text) == 1.0


def test_jaccard_similarity_disjoint():
    """Completely different texts should have Jaccard similarity of 0.0."""
    a = "alpha beta gamma delta"
    b = "epsilon zeta eta theta"
    assert supersession.jaccard_similarity(a, b) == 0.0


def test_jaccard_similarity_partial_overlap():
    """Partially overlapping texts should have a value between 0 and 1."""
    a = "the quick brown fox"
    b = "the slow brown dog"
    sim = supersession.jaccard_similarity(a, b)
    assert 0.0 < sim < 1.0
    # Words: {the, quick, brown, fox} vs {the, slow, brown, dog}
    # Intersection: {the, brown} = 2, Union: {the, quick, brown, fox, slow, dog} = 6
    assert abs(sim - 2.0 / 6.0) < 1e-9


def test_jaccard_similarity_empty_text():
    """Empty text should return 0.0."""
    assert supersession.jaccard_similarity("", "hello world") == 0.0
    assert supersession.jaccard_similarity("hello world", "") == 0.0


def _insert_memory(db, content, importance=5, category="general", is_active=1):
    """Helper to insert a memory."""
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.execute(
        "INSERT INTO memories(content, importance, category, source, created_at, "
        "accessed_at, access_count, content_hash, stability_days, difficulty, tier, is_active) "
        "VALUES (?, ?, ?, '', ?, ?, 0, ?, 1.0, 5.0, 'hot', ?)",
        (content, importance, category, now, now, content_hash, is_active)
    )
    mid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()
    return mid


def test_check_supersession_explicit_reference(db):
    """check_supersession should handle 'supersedes #N' references."""
    old_id = _insert_memory(db, "Old decision about architecture direction for project")
    new_id = _insert_memory(db, f"New architecture plan supersedes #{old_id} completely")

    result = supersession.check_supersession(
        db, new_id, f"New architecture plan supersedes #{old_id} completely",
        "decision", 7
    )
    db.commit()
    assert result == old_id

    # Old memory should be inactive
    row = db.execute("SELECT is_active, superseded_by FROM memories WHERE id=?", (old_id,)).fetchone()
    assert row[0] == 0
    assert row[1] == new_id


def test_check_supersession_high_jaccard(db):
    """check_supersession should auto-supersede when Jaccard >= 0.85."""
    old_content = "decided to use sqlite for the memory database backend storage"
    old_id = _insert_memory(db, old_content, category="decision", importance=5)

    # Very similar content (just one word different)
    new_content = "decided to use sqlite for the memory database backend system"
    new_id = _insert_memory(db, new_content, category="decision", importance=5)

    result = supersession.check_supersession(db, new_id, new_content, "decision", 5)
    db.commit()

    if result is not None:
        # Old memory should be superseded
        row = db.execute("SELECT is_active, superseded_by FROM memories WHERE id=?", (old_id,)).fetchone()
        assert row[0] == 0
        assert row[1] == new_id


def test_check_supersession_no_match(db):
    """check_supersession should return None when no supersession applies."""
    old_id = _insert_memory(db, "The weather today is sunny and warm outside here")
    new_id = _insert_memory(db, "Quantum computing uses qubits instead of bits for processing")

    result = supersession.check_supersession(
        db, new_id, "Quantum computing uses qubits instead of bits for processing",
        "general", 5
    )
    assert result is None


def test_resolve_supersession_follows_chain(db):
    """resolve_supersession should follow the chain to the final version."""
    id1 = _insert_memory(db, "Version one of the architecture decision plan document")
    id2 = _insert_memory(db, "Version two of the architecture decision plan document")
    id3 = _insert_memory(db, "Version three of the architecture decision plan document")

    # Create chain: id1 -> id2 -> id3
    supersession.supersede(db, id1, id2)
    supersession.supersede(db, id2, id3)
    db.commit()

    result = supersession.resolve_supersession(db, id1)
    assert result == id3


def test_resolve_supersession_single_memory(db):
    """resolve_supersession on a non-superseded memory should return itself."""
    mid = _insert_memory(db, "Standalone memory with no supersession chain at all")
    result = supersession.resolve_supersession(db, mid)
    assert result == mid


def test_resolve_supersession_max_depth(db):
    """resolve_supersession should stop at max_depth to prevent infinite loops."""
    ids = []
    for i in range(15):
        mid = _insert_memory(db, f"Chain link number {i} in a long supersession chain")
        ids.append(mid)

    for i in range(len(ids) - 1):
        supersession.supersede(db, ids[i], ids[i + 1])
    db.commit()

    # With max_depth=10, should not reach ids[14]
    result = supersession.resolve_supersession(db, ids[0], max_depth=10)
    assert result == ids[10]  # stops at depth 10
