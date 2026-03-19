#!/usr/bin/env python3
"""Hippocampus — memory organ.

Production-quality memory system. Stores memories with importance scoring,
full-text search, smart retrieval (recency × importance × relevance),
deduplication, and consolidation.

The brain queries memory.db directly (high bandwidth, same body part).
Other organs send memories via stimulus: "remember: <content>"
Remote body parts query via nervous system (future).

Each cycle:
1. Consume stimulus — store new memories
2. Consolidate — merge similar memories, decay unaccessed ones
3. Report health

Schema mirrors the proven knobert memory.db architecture.
"""
import os, sys, sqlite3, subprocess, hashlib, math
from pathlib import Path
from datetime import datetime, timezone, timedelta

DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("MEMORY_DB", str(DIR / "memory.db"))
CONF_DIR = os.environ.get("CONF_DIR", str(DIR.parent))

# Thresholds (configurable via environment)
MAX_MEMORIES = int(os.environ.get("MAX_MEMORIES", "10000"))
SIMILAR_THRESHOLD = float(os.environ.get("SIMILAR_THRESHOLD", "0.85"))
STALE_DAYS = int(os.environ.get("STALE_DAYS", "30"))


def log(msg):
    print(f"hippocampus: {msg}", file=sys.stderr)


def init_db(db):
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


# =========================================================================
#  Storage
# =========================================================================

def store(db, content, importance=5, category="general", source=""):
    """Store a memory with dedup by content hash.

    Returns the memory id if new, None if duplicate (access count bumped).
    """
    content = content.strip()
    if not content:
        return None

    content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Check for exact duplicate
    existing = db.execute(
        "SELECT id, importance FROM memories WHERE content_hash = ?", (content_hash,)
    ).fetchone()

    if existing:
        # Bump access, upgrade importance if new is higher
        new_imp = max(existing[1], importance)
        db.execute(
            "UPDATE memories SET accessed_at=?, access_count=access_count+1, importance=? WHERE id=?",
            (now, new_imp, existing[0])
        )
        return None

    db.execute(
        "INSERT INTO memories(content, importance, category, source, created_at, accessed_at, access_count, content_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
        (content, importance, category, source, now, now, content_hash)
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def supersede(db, old_id, new_id):
    """Mark an old memory as superseded by a new one."""
    db.execute(
        "UPDATE memories SET superseded_by=?, is_active=0 WHERE id=?",
        (new_id, old_id)
    )


# =========================================================================
#  Retrieval — smart scoring: recency × importance × relevance
# =========================================================================

def search(db, query, limit=10, category=None):
    """Search memories using FTS5 BM25 + importance + recency scoring."""
    if not query or not query.strip():
        return []

    now = datetime.now(timezone.utc)

    # FTS5 search with BM25 ranking
    sql = """
        SELECT m.id, m.content, m.importance, m.category, m.source,
               m.created_at, m.accessed_at, m.access_count, rank
        FROM memories_fts fts
        JOIN memories m ON m.id = fts.rowid
        WHERE memories_fts MATCH ? AND m.is_active = 1
    """
    params = [query]
    if category:
        sql += " AND m.category = ?"
        params.append(category)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit * 3)  # over-fetch for re-ranking

    rows = db.execute(sql, params).fetchall()

    # Re-rank with composite score: relevance (BM25) × importance × recency
    scored = []
    for row in rows:
        bm25_score = -row[8]  # FTS5 rank is negative (lower = better)
        imp = row[2] / 10.0
        created = datetime.fromisoformat(row[5].replace("Z", "+00:00"))
        age_days = max((now - created).total_seconds() / 86400, 0.01)
        recency = 1.0 / (1.0 + math.log(1 + age_days))

        composite = (0.4 * bm25_score) + (0.35 * imp) + (0.25 * recency)
        scored.append((composite, row))

    scored.sort(key=lambda x: -x[0])

    # Update access timestamps for returned results
    access_now = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    results = []
    for _, row in scored[:limit]:
        db.execute(
            "UPDATE memories SET accessed_at=?, access_count=access_count+1 WHERE id=?",
            (access_now, row[0])
        )
        results.append(row)
    db.commit()

    return results


def recent(db, limit=10, category=None):
    """Get most recent active memories."""
    sql = "SELECT id, content, importance, category, source, created_at, accessed_at, access_count FROM memories WHERE is_active=1"
    params = []
    if category:
        sql += " AND category=?"
        params.append(category)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return db.execute(sql, params).fetchall()


def by_importance(db, limit=10, min_importance=7):
    """Get high-importance active memories."""
    return db.execute(
        "SELECT id, content, importance, category, source, created_at FROM memories "
        "WHERE is_active=1 AND importance>=? ORDER BY importance DESC, created_at DESC LIMIT ?",
        (min_importance, limit)
    ).fetchall()


# =========================================================================
#  Consolidation
# =========================================================================

def consolidate(db):
    """Consolidate memories each cycle.

    1. Decay: mark very old, low-importance, unaccessed memories as inactive
    2. Prune: if over MAX_MEMORIES, remove lowest-value inactive memories
    """
    now = datetime.now(timezone.utc)
    decayed = 0
    pruned = 0

    # Phase 1: Decay — mark stale low-value memories as inactive
    cutoff = (now - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    decayed = db.execute("""
        UPDATE memories SET is_active = 0
        WHERE is_active = 1
          AND importance < 5
          AND access_count < 2
          AND accessed_at < ?
    """, (cutoff,)).rowcount

    if decayed > 0:
        log(f"decayed {decayed} stale low-value memories")

    # Phase 2: Prune — hard delete inactive memories if over limit
    total = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    if total > MAX_MEMORIES:
        to_delete = total - MAX_MEMORIES
        pruned = db.execute("""
            DELETE FROM memories WHERE id IN (
                SELECT id FROM memories
                WHERE is_active = 0
                ORDER BY importance ASC, access_count ASC, created_at ASC
                LIMIT ?
            )
        """, (to_delete,)).rowcount

        if pruned > 0:
            log(f"pruned {pruned} inactive memories")

    return decayed, pruned


# =========================================================================
#  Stimulus parsing
# =========================================================================

def parse_remember(line):
    """Parse a 'remember' stimulus line.

    Formats:
        remember: <content>
        remember important: <content>     (importance 8)
        remember critical: <content>      (importance 10)
        remember <category>: <content>    (custom category, importance 5)
    """
    line = line.strip()
    if not line.startswith("remember"):
        return None

    rest = line[len("remember"):].strip()
    if rest.startswith(":"):
        return {"content": rest[1:].strip(), "importance": 5, "category": "general"}

    if rest.startswith("important:"):
        return {"content": rest[len("important:"):].strip(), "importance": 8, "category": "general"}
    if rest.startswith("critical:"):
        return {"content": rest[len("critical:"):].strip(), "importance": 10, "category": "general"}

    if ":" in rest:
        cat, content = rest.split(":", 1)
        cat = cat.strip().lower().replace(" ", "_")
        if cat and content.strip() and len(cat) < 30:
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
            mid = store(db, parsed["content"], parsed["importance"], parsed["category"], source="stimulus")
            if mid:
                stored += 1
                log(f"stored [{parsed['category']}] imp={parsed['importance']}: {parsed['content'][:60]}...")

    return stored


# =========================================================================
#  Stats
# =========================================================================

def stats(db):
    """Return memory statistics."""
    total = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    active = db.execute("SELECT COUNT(*) FROM memories WHERE is_active=1").fetchone()[0]
    avg_imp = db.execute("SELECT ROUND(AVG(importance),1) FROM memories WHERE is_active=1").fetchone()[0] or 0
    categories = db.execute(
        "SELECT category, COUNT(*) FROM memories WHERE is_active=1 GROUP BY category ORDER BY COUNT(*) DESC"
    ).fetchall()
    return {"total": total, "active": active, "avg_importance": avg_imp, "categories": categories}


# =========================================================================
#  Main cycle
# =========================================================================

def main():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    init_db(db)

    # Phase 1: Consume stimulus and store memories
    stimulus_text = consume_stimulus()
    stored = process_stimulus(db, stimulus_text)
    db.commit()

    # Phase 2: Consolidate
    decayed, pruned = consolidate(db)
    db.commit()

    # Phase 3: Health report
    s = stats(db)
    health = f"ok {s['active']} memories ({s['total']} total, stored {stored})"
    (DIR / "health.txt").write_text(health + "\n")
    log(f"active={s['active']} total={s['total']} stored={stored} decayed={decayed} pruned={pruned}")

    db.close()


if __name__ == "__main__":
    main()
