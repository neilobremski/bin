"""Clean schema for the hippocampus memory database. No migrations."""


def init_db(db):
    """Create all tables from scratch. Idempotent via IF NOT EXISTS."""
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
            is_active INTEGER DEFAULT 1,
            stability_days REAL DEFAULT 1.0,
            difficulty REAL DEFAULT 5.0,
            tier TEXT DEFAULT 'hot',
            tags TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_memories_active
            ON memories(is_active, importance DESC);
        CREATE INDEX IF NOT EXISTS idx_memories_created
            ON memories(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_memories_hash
            ON memories(content_hash);

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
            INSERT INTO memories_fts(memories_fts, rowid, content)
                VALUES('delete', old.id, old.content);
        END;
        CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content)
                VALUES('delete', old.id, old.content);
            INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
        END;

        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            aliases TEXT DEFAULT '[]',
            entity_type TEXT DEFAULT 'thing',
            summary TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);

        CREATE TABLE IF NOT EXISTS entity_memories (
            entity_id TEXT NOT NULL REFERENCES entities(id),
            memory_id INTEGER NOT NULL REFERENCES memories(id),
            relationship TEXT DEFAULT 'mentions',
            valid_from TEXT NOT NULL,
            valid_until TEXT DEFAULT NULL,
            PRIMARY KEY (entity_id, memory_id)
        );
        CREATE INDEX IF NOT EXISTS idx_em_entity
            ON entity_memories(entity_id, valid_until);
        CREATE INDEX IF NOT EXISTS idx_em_memory
            ON entity_memories(memory_id);
    """)
