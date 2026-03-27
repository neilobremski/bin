"""Tests for entities.py: create_entity, extract_and_link_entities, get_entity_context."""
import json
import hashlib
from datetime import datetime, timezone

import entities
from constants import TS_FMT


def _seed_test_entities(db):
    """Create test entities (no hardcoded seeds in production code)."""
    entities.create_entity(db, "neil", "Neil",
                           aliases=["Neil", "partner"],
                           entity_type="person",
                           summary="Neil Obremski, partner")
    entities.create_entity(db, "organism", "Organism",
                           aliases=["organism", "synthetic organism"],
                           entity_type="system",
                           summary="The synthetic organism")
    db.commit()


def _insert_memory(db, content, importance=5):
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
    now = datetime.now(timezone.utc).strftime(TS_FMT)
    db.execute(
        "INSERT INTO memories(content, importance, category, source, created_at, "
        "accessed_at, access_count, content_hash, stability_days, difficulty, tier, is_active) "
        "VALUES (?, ?, 'general', '', ?, ?, 0, ?, 1.0, 5.0, 'hot', 1)",
        (content, importance, now, now, content_hash)
    )
    mid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()
    return mid


def test_create_entity(db):
    ok = entities.create_entity(db, "test", "Test Entity",
                                entity_type="thing", summary="A test")
    db.commit()
    assert ok is True
    row = db.execute("SELECT name, entity_type FROM entities WHERE id='test'").fetchone()
    assert row[0] == "Test Entity"
    assert row[1] == "thing"


def test_create_entity_duplicate_returns_false(db):
    entities.create_entity(db, "test", "Test Entity")
    db.commit()
    ok = entities.create_entity(db, "test", "Test Entity Again")
    assert ok is False


def test_extract_and_link_entities_tags_memory(db):
    _seed_test_entities(db)
    mid = _insert_memory(db, "Neil said that memory is the most important feature")
    entities.extract_and_link_entities(db, mid, "Neil said that memory is the most important feature")
    db.commit()
    links = db.execute("SELECT entity_id FROM entity_memories WHERE memory_id=?", (mid,)).fetchall()
    entity_ids = [r[0] for r in links]
    assert "neil" in entity_ids


def test_extract_and_link_entities_sets_tags(db):
    _seed_test_entities(db)
    mid = _insert_memory(db, "Neil and the synthetic organism are working together")
    entities.extract_and_link_entities(
        db, mid, "Neil and the synthetic organism are working together")
    db.commit()
    tags = db.execute("SELECT tags FROM memories WHERE id=?", (mid,)).fetchone()[0]
    assert "neil" in tags
    assert "organism" in tags


def test_extract_finds_known_aliases(db):
    _seed_test_entities(db)
    mid = _insert_memory(db, "The partner and the organism discussed the future plans")
    entities.extract_and_link_entities(
        db, mid, "The partner and the organism discussed the future plans")
    db.commit()
    links = db.execute("SELECT entity_id FROM entity_memories WHERE memory_id=?", (mid,)).fetchall()
    entity_ids = {r[0] for r in links}
    assert "neil" in entity_ids
    assert "organism" in entity_ids


def test_extract_no_match_creates_no_links(db):
    _seed_test_entities(db)
    mid = _insert_memory(db, "The weather is sunny and warm outside today here")
    entities.extract_and_link_entities(
        db, mid, "The weather is sunny and warm outside today here")
    db.commit()
    count = db.execute("SELECT COUNT(*) FROM entity_memories WHERE memory_id=?", (mid,)).fetchone()[0]
    assert count == 0


def test_get_entity_context_returns_linked_memories(db):
    _seed_test_entities(db)
    mid1 = _insert_memory(db, "Neil prefers FSRS over simple decay models", importance=8)
    mid2 = _insert_memory(db, "Neil wants the hippocampus to be autonomous", importance=7)
    entities.extract_and_link_entities(db, mid1, "Neil prefers FSRS over simple decay models")
    entities.extract_and_link_entities(db, mid2, "Neil wants the hippocampus to be autonomous")
    db.commit()
    context = entities.get_entity_context(db, [mid1, mid2])
    assert len(context) >= 1
    assert any("Neil" in block for block in context)


def test_get_entity_context_empty_ids(db):
    assert entities.get_entity_context(db, []) == []


def test_list_entities_returns_all_with_link_counts(db):
    _seed_test_entities(db)
    mid = _insert_memory(db, "Neil said the hippocampus needs more tests")
    entities.extract_and_link_entities(db, mid, "Neil said the hippocampus needs more tests")
    db.commit()
    result = entities.list_entities(db)
    assert len(result) == 2
    neil_ent = [e for e in result if e["id"] == "neil"][0]
    assert neil_ent["link_count"] >= 1


def test_list_entities_empty_db(db):
    result = entities.list_entities(db)
    assert result == []


def test_get_entity_detail_returns_entity_and_memories(db):
    _seed_test_entities(db)
    mid1 = _insert_memory(db, "Neil prefers structured memory systems", importance=8)
    mid2 = _insert_memory(db, "Neil asked about entity context injection", importance=7)
    entities.extract_and_link_entities(db, mid1, "Neil prefers structured memory systems")
    entities.extract_and_link_entities(db, mid2, "Neil asked about entity context injection")
    db.commit()
    detail = entities.get_entity_detail(db, "neil")
    assert detail is not None
    assert detail["name"] == "Neil"
    assert len(detail["linked_memories"]) >= 2


def test_get_entity_detail_nonexistent_returns_none(db):
    assert entities.get_entity_detail(db, "nonexistent") is None
