#!/usr/bin/env python3
"""Hippocampus — memory organ.

Stores memories, consolidates them over time, and provides retrieval.
The brain queries memory.db directly (high bandwidth, same body part).
Other organs send memories via stimulus: "remember: <content>"

Each cycle:
1. Consume stimulus — store new memories
2. Consolidate — collapse similar recent memories, decay old ones
3. Report health
"""
import os, sys, sqlite3, subprocess, hashlib
from pathlib import Path
from datetime import datetime, timezone

DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("MEMORY_DB", str(DIR / "memory.db"))
CONF_DIR = os.environ.get("CONF_DIR", str(DIR.parent))

# Consolidation thresholds
MAX_MEMORIES = int(os.environ.get("MAX_MEMORIES", "10000"))
CONSOLIDATION_AGE_DAYS = int(os.environ.get("CONSOLIDATION_AGE_DAYS", "7"))


def log(msg):
    print(f"hippocampus: {msg}", file=sys.stderr)


def init_db(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            importance INTEGER DEFAULT 5,
            category TEXT DEFAULT 'general',
            created_at TEXT NOT NULL,
            accessed_at TEXT,
            access_count INTEGER DEFAULT 0,
            content_hash TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memories_hash ON memories(content_hash);
        CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance DESC);
        CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC);

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


def consume_stimulus():
    """Read and clear stimulus via the stimulus CLI."""
    try:
        result = subprocess.run(
            ["stimulus", "consume", str(DIR)],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def store_memory(db, content, importance=5, category="general"):
    """Store a memory, deduplicating by content hash."""
    content = content.strip()
    if not content:
        return False

    content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Check for duplicate
    existing = db.execute(
        "SELECT id FROM memories WHERE content_hash = ?", (content_hash,)
    ).fetchone()

    if existing:
        # Update access time instead of duplicating
        db.execute(
            "UPDATE memories SET accessed_at = ?, access_count = access_count + 1 WHERE id = ?",
            (now, existing[0])
        )
        return False

    db.execute(
        "INSERT INTO memories(content, importance, category, created_at, accessed_at, access_count, content_hash) "
        "VALUES (?, ?, ?, ?, ?, 0, ?)",
        (content, importance, category, now, now, content_hash)
    )
    return True


def parse_remember(line):
    """Parse a 'remember' stimulus line.

    Formats:
        remember: <content>
        remember important: <content>     (importance 8)
        remember critical: <content>      (importance 10)
        remember <category>: <content>    (custom category)
    """
    line = line.strip()
    if not line.startswith("remember"):
        return None

    rest = line[len("remember"):].strip()
    if rest.startswith(":"):
        return {"content": rest[1:].strip(), "importance": 5, "category": "general"}

    # Check for importance modifiers
    if rest.startswith("important:"):
        return {"content": rest[len("important:"):].strip(), "importance": 8, "category": "general"}
    if rest.startswith("critical:"):
        return {"content": rest[len("critical:"):].strip(), "importance": 10, "category": "general"}

    # Check for category: content
    if ":" in rest:
        cat, content = rest.split(":", 1)
        cat = cat.strip().lower()
        if cat and content.strip():
            return {"content": content.strip(), "importance": 5, "category": cat}

    return None


def process_stimulus(db, stimulus_text):
    """Process stimulus lines, storing memories."""
    if not stimulus_text:
        return 0

    stored = 0
    for line in stimulus_text.splitlines():
        line = line.strip()
        if not line:
            continue

        parsed = parse_remember(line)
        if parsed:
            if store_memory(db, parsed["content"], parsed["importance"], parsed["category"]):
                stored += 1
                log(f"stored: {parsed['content'][:60]}...")

    return stored


def consolidate(db):
    """Consolidate memories: prune if over limit, keeping high-importance and recent."""
    count = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    if count <= MAX_MEMORIES:
        return 0

    # Delete lowest-value memories (low importance, old, rarely accessed)
    # Score = importance * 2 + recency_days_inv + log(access_count + 1)
    pruned = db.execute("""
        DELETE FROM memories WHERE id IN (
            SELECT id FROM memories
            ORDER BY importance ASC, access_count ASC, created_at ASC
            LIMIT ?
        )
    """, (count - MAX_MEMORIES,)).rowcount

    if pruned > 0:
        log(f"consolidated: pruned {pruned} low-value memories")

    return pruned


def search(db, query, limit=10):
    """Search memories by relevance (FTS5 BM25 ranking)."""
    rows = db.execute("""
        SELECT m.id, m.content, m.importance, m.category, m.created_at, m.access_count,
               rank
        FROM memories_fts fts
        JOIN memories m ON m.id = fts.rowid
        WHERE memories_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    """, (query, limit)).fetchall()

    # Update access timestamps
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for row in rows:
        db.execute(
            "UPDATE memories SET accessed_at = ?, access_count = access_count + 1 WHERE id = ?",
            (now, row[0])
        )
    db.commit()

    return rows


def main():
    os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    init_db(db)

    # Phase 1: Consume stimulus and store memories
    stimulus_text = consume_stimulus()
    stored = process_stimulus(db, stimulus_text)
    db.commit()

    # Phase 2: Consolidate
    pruned = consolidate(db)
    db.commit()

    # Phase 3: Health report
    total = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    health = f"ok {total} memories (stored {stored})"
    (DIR / "health.txt").write_text(health + "\n")
    log(f"total={total} stored={stored} pruned={pruned}")

    db.close()


if __name__ == "__main__":
    main()
