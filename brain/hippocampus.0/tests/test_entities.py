"""Tests for entities.py: seed_entities, extract_and_link_entities, get_entity_context."""
import json
import hashlib
from datetime import datetime, timezone

import entities


def test_seed_entities_creates_initial_entities(db):
    """seed_entities should populate the entities table with seed data."""
    entities.seed_entities(db)

    count = db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    assert count == len(entities.SEED_ENTITIES)

    neil = db.execute("SELECT name, entity_type FROM entities WHERE id='neil'").fetchone()
    assert neil is not None
    assert neil[0] == "Neil"
    assert neil[1] == "person"


def test_seed_entities_is_idempotent(db):
    """seed_entities called twice should not duplicate entries."""
    entities.seed_entities(db)
    entities.seed_entities(db)

    count = db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    assert count == len(entities.SEED_ENTITIES)


def _insert_memory(db, content, importance=5):
    """Helper to insert a memory and return its id."""
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.execute(
        "INSERT INTO memories(content, importance, category, source, created_at, "
        "accessed_at, access_count, content_hash, stability_days, difficulty, tier, is_active) "
        "VALUES (?, ?, 'general', '', ?, ?, 0, ?, 1.0, 5.0, 'hot', 1)",
        (content, importance, now, now, content_hash)
    )
    mid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()
    return mid


def test_extract_and_link_entities_tags_memory(db):
    """extract_and_link_entities should create entity_memories links."""
    entities.seed_entities(db)
    mid = _insert_memory(db, "Neil said that memory is the most important feature")

    entities.extract_and_link_entities(db, mid, "Neil said that memory is the most important feature")
    db.commit()

    links = db.execute(
        "SELECT entity_id FROM entity_memories WHERE memory_id=?", (mid,)
    ).fetchall()
    entity_ids = [r[0] for r in links]
    assert "neil" in entity_ids


def test_extract_and_link_entities_sets_tags(db):
    """extract_and_link_entities should set pipe-delimited tags on the memory."""
    entities.seed_entities(db)
    mid = _insert_memory(db, "Neil and the tadpole organism are working together")

    entities.extract_and_link_entities(
        db, mid, "Neil and the tadpole organism are working together"
    )
    db.commit()

    tags = db.execute("SELECT tags FROM memories WHERE id=?", (mid,)).fetchone()[0]
    assert tags is not None
    assert "neil" in tags
    assert "tadpole" in tags


def test_extract_finds_known_aliases(db):
    """extract_and_link_entities should match entity aliases (case-insensitive)."""
    entities.seed_entities(db)
    # "partner" is an alias for neil, "organism" is an alias for tadpole
    mid = _insert_memory(db, "The partner and the organism discussed the future plans")

    entities.extract_and_link_entities(
        db, mid, "The partner and the organism discussed the future plans"
    )
    db.commit()

    links = db.execute(
        "SELECT entity_id FROM entity_memories WHERE memory_id=?", (mid,)
    ).fetchall()
    entity_ids = {r[0] for r in links}
    assert "neil" in entity_ids
    assert "tadpole" in entity_ids


def test_extract_no_match_creates_no_links(db):
    """extract_and_link_entities with no matching entities should create no links."""
    entities.seed_entities(db)
    mid = _insert_memory(db, "The weather is sunny and warm outside today here")

    entities.extract_and_link_entities(
        db, mid, "The weather is sunny and warm outside today here"
    )
    db.commit()

    count = db.execute(
        "SELECT COUNT(*) FROM entity_memories WHERE memory_id=?", (mid,)
    ).fetchone()[0]
    assert count == 0


def test_get_entity_context_returns_linked_memories(db):
    """get_entity_context should return context blocks for linked entities."""
    entities.seed_entities(db)
    mid1 = _insert_memory(db, "Neil prefers FSRS over simple decay models", importance=8)
    mid2 = _insert_memory(db, "Neil wants the hippocampus to be autonomous", importance=7)

    entities.extract_and_link_entities(db, mid1, "Neil prefers FSRS over simple decay models")
    entities.extract_and_link_entities(db, mid2, "Neil wants the hippocampus to be autonomous")
    db.commit()

    context = entities.get_entity_context(db, [mid1, mid2])
    assert len(context) >= 1
    # Should contain Neil's context
    assert any("Neil" in block for block in context)


def test_get_entity_context_empty_ids(db):
    """get_entity_context with empty list returns empty."""
    assert entities.get_entity_context(db, []) == []


def test_list_entities_returns_all_with_link_counts(db):
    """list_entities should return all entities with their link counts."""
    entities.seed_entities(db)
    mid = _insert_memory(db, "Neil said the hippocampus needs more tests")
    entities.extract_and_link_entities(db, mid, "Neil said the hippocampus needs more tests")
    db.commit()

    result = entities.list_entities(db)
    assert len(result) == len(entities.SEED_ENTITIES)

    neil_ent = [e for e in result if e["id"] == "neil"][0]
    assert neil_ent["name"] == "Neil"
    assert neil_ent["entity_type"] == "person"
    assert neil_ent["link_count"] >= 1


def test_list_entities_empty_db(db):
    """list_entities on empty entities table returns empty list."""
    result = entities.list_entities(db)
    assert result == []


def test_get_entity_detail_returns_entity_and_memories(db):
    """get_entity_detail should return entity info plus linked memories."""
    entities.seed_entities(db)
    mid1 = _insert_memory(db, "Neil prefers structured memory systems", importance=8)
    mid2 = _insert_memory(db, "Neil asked about entity context injection", importance=7)
    entities.extract_and_link_entities(db, mid1, "Neil prefers structured memory systems")
    entities.extract_and_link_entities(db, mid2, "Neil asked about entity context injection")
    db.commit()

    detail = entities.get_entity_detail(db, "neil")
    assert detail is not None
    assert detail["name"] == "Neil"
    assert detail["entity_type"] == "person"
    assert len(detail["linked_memories"]) >= 2
    # Memories should be sorted by importance DESC
    importances = [m["importance"] for m in detail["linked_memories"]]
    assert importances == sorted(importances, reverse=True)


def test_get_entity_detail_nonexistent_returns_none(db):
    """get_entity_detail with nonexistent id returns None."""
    entities.seed_entities(db)
    assert entities.get_entity_detail(db, "nonexistent") is None
