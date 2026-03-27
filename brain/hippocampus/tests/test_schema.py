"""Tests for schema.py: init_db creates all tables."""


def test_init_db_creates_memories_table(db):
    rows = db.execute("PRAGMA table_info(memories)").fetchall()
    col_names = {r[1] for r in rows}
    for col in ("id", "content", "importance", "category", "content_hash",
                "is_active", "superseded_by", "stability_days", "difficulty",
                "tier", "tags", "source"):
        assert col in col_names, f"missing column: {col}"


def test_init_db_creates_fts_table(db):
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memories_fts'"
    ).fetchall()
    assert len(rows) == 1


def test_init_db_creates_entities_table(db):
    rows = db.execute("PRAGMA table_info(entities)").fetchall()
    col_names = {r[1] for r in rows}
    assert "name" in col_names
    assert "aliases" in col_names
    assert "entity_type" in col_names


def test_init_db_creates_entity_memories_table(db):
    rows = db.execute("PRAGMA table_info(entity_memories)").fetchall()
    col_names = {r[1] for r in rows}
    assert "entity_id" in col_names
    assert "memory_id" in col_names


def test_init_db_is_idempotent(db):
    """Running init_db twice should not raise."""
    import schema
    schema.init_db(db)
    schema.init_db(db)
    rows = db.execute("PRAGMA table_info(memories)").fetchall()
    assert len(rows) > 0
