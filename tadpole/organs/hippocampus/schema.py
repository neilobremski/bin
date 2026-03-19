"""Schema initialization and migration for the hippocampus memory database."""
import sqlite3
from datetime import datetime, timezone


# Ordered list of (migration_id, sql_statement) tuples.
# Migration is idempotent: tracked in schema_migrations table.
MIGRATIONS = [
    ("v2_001_stability",
     "ALTER TABLE memories ADD COLUMN stability_days REAL DEFAULT 1.0"),
    ("v2_002_difficulty",
     "ALTER TABLE memories ADD COLUMN difficulty REAL DEFAULT 5.0"),
    # RESERVED: not yet implemented. Migration kept for DB compatibility.
    # last_pe_score, labile_until, recon_count — columns created but not
    # read or written by any production code. Keep migrations so existing
    # DBs don't re-run them.
    ("v2_003_pe_score",
     "ALTER TABLE memories ADD COLUMN last_pe_score REAL DEFAULT 0.0"),
    ("v2_004_labile",
     "ALTER TABLE memories ADD COLUMN labile_until TEXT DEFAULT NULL"),
    ("v2_005_recon_count",
     "ALTER TABLE memories ADD COLUMN recon_count INTEGER DEFAULT 0"),
    ("v2_006_tier",
     "ALTER TABLE memories ADD COLUMN tier TEXT DEFAULT 'hot'"),
    ("v2_010_entities", """CREATE TABLE IF NOT EXISTS entities (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        aliases TEXT DEFAULT '[]',
        entity_type TEXT DEFAULT 'thing',
        summary TEXT DEFAULT '',
        properties TEXT DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )"""),
    ("v2_010b_entities_idx",
     "CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type)"),
    ("v2_011_entity_memories", """CREATE TABLE IF NOT EXISTS entity_memories (
        entity_id TEXT NOT NULL REFERENCES entities(id),
        memory_id INTEGER NOT NULL REFERENCES memories(id),
        relationship TEXT DEFAULT 'mentions',
        valid_from TEXT NOT NULL,
        valid_until TEXT DEFAULT NULL,
        PRIMARY KEY (entity_id, memory_id)
    )"""),
    ("v2_011b_em_idx1",
     "CREATE INDEX IF NOT EXISTS idx_em_entity ON entity_memories(entity_id, valid_until)"),
    ("v2_011c_em_idx2",
     "CREATE INDEX IF NOT EXISTS idx_em_memory ON entity_memories(memory_id)"),
    # Reserved for future use: associations table is created but not yet
    # read or written by any code. Keep the migration for DB compatibility.
    ("v2_012_associations", """CREATE TABLE IF NOT EXISTS associations (
        source_id INTEGER NOT NULL REFERENCES memories(id),
        target_id INTEGER NOT NULL REFERENCES memories(id),
        link_type TEXT DEFAULT 'related',
        strength REAL DEFAULT 1.0,
        created_at TEXT NOT NULL,
        PRIMARY KEY (source_id, target_id)
    )"""),
    ("v2_012b_assoc_idx1",
     "CREATE INDEX IF NOT EXISTS idx_assoc_source ON associations(source_id)"),
    ("v2_012c_assoc_idx2",
     "CREATE INDEX IF NOT EXISTS idx_assoc_target ON associations(target_id)"),
    ("v2_014_tags",
     "ALTER TABLE memories ADD COLUMN tags TEXT DEFAULT ''"),
    ("v2_013_consolidation_log", """CREATE TABLE IF NOT EXISTS consolidation_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation TEXT NOT NULL,
        source_ids TEXT NOT NULL,
        result_id INTEGER,
        summary TEXT,
        verified INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    )"""),
]


def init_db(db):
    """Create base schema (v1 tables)."""
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            importance INTEGER DEFAULT 5,
            category TEXT DEFAULT 'general',
            source TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            accessed_at TEXT NOT NULL,
            access_count INTEGER DEFAULT 0,
            content_hash TEXT NOT NULL UNIQUE,
            superseded_by INTEGER DEFAULT NULL,
            is_active INTEGER DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_memories_active ON memories(is_active, importance DESC);
        CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);

        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            content,
            content='memories',
            content_rowid='id'
        );

        -- Triggers to keep FTS in sync
        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
        END;
        CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.id, old.content);
        END;
        CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.id, old.content);
            INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
        END;
    """)


def migrate(db):
    """Apply pending v2 migrations. Idempotent."""
    db.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
        id TEXT PRIMARY KEY, applied_at TEXT NOT NULL
    )""")
    applied = {r[0] for r in db.execute("SELECT id FROM schema_migrations").fetchall()}
    for mid, sql in MIGRATIONS:
        if mid not in applied:
            try:
                db.execute(sql)
                db.execute(
                    "INSERT INTO schema_migrations(id, applied_at) VALUES(?, ?)",
                    (mid, datetime.now(timezone.utc).isoformat())
                )
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
                # Column already exists (e.g. from a previous partial migration)
                db.execute(
                    "INSERT OR IGNORE INTO schema_migrations(id, applied_at) VALUES(?, ?)",
                    (mid, datetime.now(timezone.utc).isoformat())
                )
    db.commit()


def backfill_stability(db):
    """Set initial stability_days and difficulty for existing memories.

    Only updates rows that still have defaults (stability_days=1.0)
    and have been accessed at least once.
    """
    db.execute("""
        UPDATE memories SET
            stability_days = CASE
                WHEN access_count >= 10 THEN 30.0
                WHEN access_count >= 5 THEN 14.0
                WHEN access_count >= 2 THEN 7.0
                ELSE 1.0
            END,
            difficulty = MAX(1.0, MIN(10.0, 11.0 - importance))
        WHERE stability_days = 1.0 AND access_count > 0
    """)
    db.commit()
