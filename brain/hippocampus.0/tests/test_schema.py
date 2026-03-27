"""Tests for schema.py: init_db, migrate, backfill_stability."""
import sqlite3
import schema


def test_init_db_creates_memories_table(db):
    """init_db should create the memories table with expected columns."""
    rows = db.execute("PRAGMA table_info(memories)").fetchall()
    col_names = {r[1] for r in rows}
    assert "id" in col_names
    assert "content" in col_names
    assert "importance" in col_names
    assert "category" in col_names
    assert "content_hash" in col_names
    assert "is_active" in col_names
    assert "superseded_by" in col_names


def test_init_db_creates_fts_table(db):
    """init_db should create the memories_fts virtual table."""
    # FTS5 tables show up in sqlite_master
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memories_fts'"
    ).fetchall()
    assert len(rows) == 1


def test_migrate_creates_v2_columns(db):
    """migrate should add stability_days, difficulty, tier, tags columns."""
    rows = db.execute("PRAGMA table_info(memories)").fetchall()
    col_names = {r[1] for r in rows}
    assert "stability_days" in col_names
    assert "difficulty" in col_names
    assert "tier" in col_names
    assert "tags" in col_names


def test_migrate_creates_entities_table(db):
    """migrate should create the entities table."""
    rows = db.execute("PRAGMA table_info(entities)").fetchall()
    assert len(rows) > 0
    col_names = {r[1] for r in rows}
    assert "name" in col_names
    assert "aliases" in col_names
    assert "entity_type" in col_names


def test_migrate_creates_entity_memories_table(db):
    """migrate should create the entity_memories join table."""
    rows = db.execute("PRAGMA table_info(entity_memories)").fetchall()
    assert len(rows) > 0
    col_names = {r[1] for r in rows}
    assert "entity_id" in col_names
    assert "memory_id" in col_names


def test_migrate_creates_associations_table(db):
    """migrate should create the associations table."""
    rows = db.execute("PRAGMA table_info(associations)").fetchall()
    assert len(rows) > 0
    col_names = {r[1] for r in rows}
    assert "source_id" in col_names
    assert "target_id" in col_names
    assert "strength" in col_names


def test_migrate_creates_consolidation_log_table(db):
    """migrate should create the consolidation_log table."""
    rows = db.execute("PRAGMA table_info(consolidation_log)").fetchall()
    assert len(rows) > 0


def test_migrate_is_idempotent(db):
    """Running migrate twice should not raise errors."""
    schema.migrate(db)  # second call (first was in fixture)
    schema.migrate(db)  # third call for good measure
    # If we get here without error, idempotency holds
    count = db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert count == len(schema.MIGRATIONS)


def test_backfill_stability_updates_accessed_memories(db):
    """backfill_stability should set stability_days based on access_count."""
    now = "2026-03-19T12:00:00Z"
    # Insert a memory with access_count=10, stability_days=1.0 (default)
    db.execute(
        "INSERT INTO memories(content, importance, category, source, created_at, "
        "accessed_at, access_count, content_hash, stability_days, difficulty) "
        "VALUES (?, 5, 'general', '', ?, ?, 10, 'hash_a', 1.0, 5.0)",
        ("High access memory", now, now)
    )
    # Insert a memory with access_count=0 (should NOT be updated)
    db.execute(
        "INSERT INTO memories(content, importance, category, source, created_at, "
        "accessed_at, access_count, content_hash, stability_days, difficulty) "
        "VALUES (?, 5, 'general', '', ?, ?, 0, 'hash_b', 1.0, 5.0)",
        ("Zero access memory", now, now)
    )
    db.commit()

    schema.backfill_stability(db)

    # access_count=10 -> stability_days=30.0
    row_a = db.execute(
        "SELECT stability_days FROM memories WHERE content_hash='hash_a'"
    ).fetchone()
    assert row_a[0] == 30.0

    # access_count=0 -> unchanged (still 1.0)
    row_b = db.execute(
        "SELECT stability_days FROM memories WHERE content_hash='hash_b'"
    ).fetchone()
    assert row_b[0] == 1.0


def test_backfill_stability_sets_difficulty_from_importance(db):
    """backfill_stability should set difficulty = 11 - importance (clamped)."""
    now = "2026-03-19T12:00:00Z"
    db.execute(
        "INSERT INTO memories(content, importance, category, source, created_at, "
        "accessed_at, access_count, content_hash, stability_days, difficulty) "
        "VALUES (?, 8, 'general', '', ?, ?, 3, 'hash_c', 1.0, 5.0)",
        ("Important memory", now, now)
    )
    db.commit()

    schema.backfill_stability(db)

    row = db.execute(
        "SELECT difficulty FROM memories WHERE content_hash='hash_c'"
    ).fetchone()
    # difficulty = 11 - 8 = 3.0
    assert row[0] == 3.0
