#!/usr/bin/env python3
"""Hippocampus v2 — memory organ.

Production-quality memory system with FSRS-inspired stability tracking,
five-factor composite scoring, tiered retrieval, admission control,
auto-supersession, and category-aware consolidation.

The brain queries memory.db directly (high bandwidth, same body part).
Other organs send memories via stimulus: "remember: <content>"
Remote body parts query via nervous system (future).

Each cycle:
1. Consume stimulus — store new memories (with admission control)
2. Consolidate — FSRS decay, merge similar, retier, prune
3. Report health

Schema mirrors the proven knobert memory.db architecture, extended with
FSRS columns, tier tracking, and supporting tables.
"""
import os, sys, sqlite3, subprocess, hashlib, math, json, re
from pathlib import Path
from datetime import datetime, timezone, timedelta

DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("MEMORY_DB", str(DIR / "memory.db"))
CONF_DIR = os.environ.get("CONF_DIR", str(DIR.parent))

# Thresholds (configurable via environment)
MAX_MEMORIES = int(os.environ.get("MAX_MEMORIES", "10000"))
SIMILAR_THRESHOLD = float(os.environ.get("SIMILAR_THRESHOLD", "0.85"))
STALE_DAYS = int(os.environ.get("STALE_DAYS", "30"))
HOT_TIER_SIZE = int(os.environ.get("HOT_TIER_SIZE", "500"))

# Optional LLM integration (off by default)
USE_LLM = os.environ.get("HIPPOCAMPUS_USE_LLM", "") == "1"

# Track total queries this session (for UCB exploration bonus)
_total_queries = 0


def log(msg):
    print(f"hippocampus: {msg}", file=sys.stderr)


# =========================================================================
#  Category Configuration (Appendix C)
# =========================================================================

CATEGORY_CONFIG = {
    "neil_insight": {
        "min_importance": 7,
        "ttl_days": None,       # never expires
        "protected": True,
        "tier_override": "hot",
    },
    "decision": {
        "min_importance": 5,
        "ttl_days": 30,
        "protected": False,
        "tier_override": None,
    },
    "observation": {
        "min_importance": 1,
        "ttl_days": 7,
        "protected": False,
        "tier_override": None,
    },
    "research": {
        "min_importance": 3,
        "ttl_days": 14,
        "protected": False,
        "tier_override": None,
    },
    "system": {
        "min_importance": 1,
        "ttl_days": 1,
        "protected": False,
        "tier_override": None,
    },
    "general": {
        "min_importance": 1,
        "ttl_days": 14,
        "protected": False,
        "tier_override": None,
    },
}


# =========================================================================
#  Small-LLM integration (optional, controlled by HIPPOCAMPUS_USE_LLM=1)
# =========================================================================

def _call_small_llm(system_prompt, user_prompt, timeout=30):
    """Call small-llm CLI and return its output, or None on failure."""
    try:
        result = subprocess.run(
            ["small-llm", "-s", system_prompt, user_prompt],
            capture_output=True, text=True, timeout=timeout
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def score_importance(content):
    """Ask small-llm to rate a memory's importance 1-10.

    Returns an integer 1-10, or 5 (default) if the LLM is unavailable
    or returns an unparseable response.
    """
    if not USE_LLM:
        return 5

    system = (
        "You are a memory importance scorer. Rate the following memory on a "
        "scale of 1-10 where 1 is trivial noise and 10 is critical information "
        "that must never be forgotten. Respond with ONLY a single integer."
    )
    response = _call_small_llm(system, content)
    if response:
        for token in response.split():
            try:
                score = int(token)
                if 1 <= score <= 10:
                    return score
            except ValueError:
                continue
    return 5


def check_similar(content, candidates):
    """Ask small-llm if content is similar to any candidate memories.

    Args:
        content: the new memory text
        candidates: list of (id, existing_content) tuples

    Returns:
        The id of the most similar memory, or None if no match.
    """
    if not USE_LLM or not candidates:
        return None

    numbered = []
    for i, (mid, text) in enumerate(candidates, 1):
        numbered.append(f"{i}. {text[:200]}")
    candidate_text = "\n".join(numbered)

    system = (
        "You compare memories for similarity. Given a NEW memory and a numbered "
        "list of EXISTING memories, respond with ONLY the number of the existing "
        "memory that says the same thing as the new one. If none are similar, "
        "respond with 0."
    )
    user = f"NEW: {content[:300]}\n\nEXISTING:\n{candidate_text}"

    response = _call_small_llm(system, user)
    if response:
        for token in response.split():
            try:
                idx = int(token)
                if 1 <= idx <= len(candidates):
                    return candidates[idx - 1][0]
                if idx == 0:
                    return None
            except ValueError:
                continue
    return None


# =========================================================================
#  Jaccard Similarity (Appendix B)
# =========================================================================

def jaccard_similarity(text_a, text_b):
    """Word-level Jaccard similarity: |A intersect B| / |A union B|."""
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


# =========================================================================
#  FSRS Retrievability (Appendix A)
# =========================================================================

def retrievability(accessed_at_str, stability_days, now):
    """FSRS v4 power-law forgetting curve.

    R(t, S) = (1 + t/(9*S))^(-1)

    At t=0: R=1.0 (just accessed)
    At t=S: R=0.9 (stability is the 90% threshold)
    At t=9*S: R=0.5
    """
    if isinstance(accessed_at_str, str):
        accessed_at = datetime.fromisoformat(accessed_at_str.replace("Z", "+00:00"))
    else:
        accessed_at = accessed_at_str
    elapsed = max((now - accessed_at).total_seconds() / 86400.0, 0.0)
    return 1.0 / (1.0 + elapsed / (9.0 * max(stability_days, 0.1)))


# =========================================================================
#  Schema and Migration (Step 1)
# =========================================================================

# Ordered list of (migration_id, sql_statement) tuples.
# Migration is idempotent: tracked in schema_migrations table.
MIGRATIONS = [
    ("v2_001_stability",
     "ALTER TABLE memories ADD COLUMN stability_days REAL DEFAULT 1.0"),
    ("v2_002_difficulty",
     "ALTER TABLE memories ADD COLUMN difficulty REAL DEFAULT 5.0"),
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


# =========================================================================
#  Admission Control (Step 2)
# =========================================================================

ADMISSION_RULES = [
    # (pattern, action, importance_override)
    (r"^(ok|got it|sure|yes|no|thanks)$", "reject", None),
    (r"^health check", "accept_ttl", 3),
    (r"(decided|decision|commit)", "accept", 7),
    (r"neil (said|told|asked|wants|prefers)", "accept", 8),
    (r"(error|fail|crash|exception)", "accept", 6),
    (r"(supersedes|replaces|overrides)", "accept", 7),
]

# Dedup window: reject memories with identical hash within this many seconds
_recent_hashes = {}  # hash -> timestamp (in-process dedup window)
DEDUP_WINDOW_SECONDS = 60
MAX_STORE_RATE = 100  # max memories per cycle
_store_count_this_cycle = 0


def admit_memory(content, importance=5, category="general"):
    """Decide whether to store a memory.

    Returns (should_store: bool, adjusted_importance: int, adjusted_category: str)
    """
    global _store_count_this_cycle
    content_lower = content.lower().strip()

    # Rule 1: Too short = noise
    if len(content_lower) < 10:
        return False, importance, category

    # Rule 2: Max rate per cycle
    if _store_count_this_cycle >= MAX_STORE_RATE:
        return False, importance, category

    # Rule 3: Pattern-based admission
    for pattern, action, imp_override in ADMISSION_RULES:
        if re.search(pattern, content_lower):
            if action == "reject":
                return False, importance, category
            if imp_override and importance == 5:  # only override default
                importance = imp_override
            break

    # Rule 4: In-process dedup window (prevents burst duplicates)
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
    now_ts = datetime.now(timezone.utc).timestamp()
    if content_hash in _recent_hashes:
        if now_ts - _recent_hashes[content_hash] < DEDUP_WINDOW_SECONDS:
            return False, importance, category
    _recent_hashes[content_hash] = now_ts

    # Apply category minimum importance
    config = CATEGORY_CONFIG.get(category, CATEGORY_CONFIG["general"])
    importance = max(importance, config["min_importance"])

    return True, importance, category


# =========================================================================
#  FSRS On-Access Updates (Step 3)
# =========================================================================

def on_memory_used(db, memory_id, was_relevant):
    """FSRS-inspired stability update on memory access.

    Called after retrieval when relevance is evaluated.

    If relevant:  S_new = S * (1.0 + gain_factor)
    If irrelevant: S_new = S * 0.9

    The gain_factor is higher when:
    - The memory is difficult (low stability relative to its age)
    - The memory was retrieved at low retrievability (desirable difficulty)
    """
    now = datetime.now(timezone.utc)
    row = db.execute(
        "SELECT accessed_at, stability_days, difficulty, importance, access_count "
        "FROM memories WHERE id=?", (memory_id,)
    ).fetchone()

    if not row:
        return

    accessed_at, stability, difficulty, importance, access_count = row
    last_access = datetime.fromisoformat(accessed_at.replace("Z", "+00:00"))
    elapsed_days = max((now - last_access).total_seconds() / 86400.0, 0.01)

    # Current retrievability
    r = 1.0 / (1.0 + elapsed_days / (9.0 * max(stability, 0.1)))

    now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    if was_relevant:
        # Desirable difficulty: lower retrievability at recall = bigger stability gain
        difficulty_factor = (11.0 - difficulty) / 10.0
        retrievability_bonus = math.exp(0.5 * (1.0 - r)) - 1.0
        gain = 0.1 + 0.3 * difficulty_factor * retrievability_bonus

        # Diminishing returns after many accesses
        if access_count > 20:
            gain *= 0.5

        new_stability = min(365.0, stability * (1.0 + gain))
        new_difficulty = max(1.0, difficulty - 0.1)
    else:
        new_stability = max(0.5, stability * 0.9)
        new_difficulty = min(10.0, difficulty + 0.1)

    db.execute("""
        UPDATE memories SET
            stability_days=?, difficulty=?, accessed_at=?, access_count=access_count+1
        WHERE id=?
    """, (new_stability, new_difficulty, now_str, memory_id))


# =========================================================================
#  Composite Scoring v2 (Step 4)
# =========================================================================

def composite_score(bm25_rank, importance, created_at_str, access_count,
                    stability_days, total_queries, now):
    """Five-factor composite retrieval score.

    All factors are normalized to [0, 1] before weighting.

    Weights: relevance 0.35, importance 0.25, recency 0.15,
             FSRS retrievability 0.15, UCB exploration 0.10
    """
    # Factor 1: Relevance (BM25) -- weight 0.35
    bm25_raw = -bm25_rank
    relevance = 1.0 / (1.0 + math.exp(-bm25_raw))  # sigmoid normalization

    # Factor 2: Importance -- weight 0.25
    imp = importance / 10.0

    # Factor 3: Recency -- weight 0.15
    if isinstance(created_at_str, str):
        created = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
    else:
        created = created_at_str
    age_days = max((now - created).total_seconds() / 86400.0, 0.01)
    recency = 1.0 / (1.0 + math.log(1.0 + age_days))

    # Factor 4: Stability (FSRS-inspired) -- weight 0.15
    # Use age since creation as proxy for elapsed time in score context
    r = 1.0 / (1.0 + age_days / (9.0 * max(stability_days, 0.1)))

    # Factor 5: Exploration bonus (UCB-inspired) -- weight 0.10
    exploration = math.sqrt(2.0 * math.log(total_queries + 1) / (access_count + 1))
    exploration = min(1.0, exploration)

    score = (
        0.35 * relevance +
        0.25 * imp +
        0.15 * recency +
        0.15 * r +
        0.10 * exploration
    )
    return score


# =========================================================================
#  Stimulus consumption
# =========================================================================

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
#  Storage (with admission control and auto-supersession)
# =========================================================================

def store(db, content, importance=5, category="general", source=""):
    """Store a memory with dedup by content hash.

    When HIPPOCAMPUS_USE_LLM=1:
    - Auto-scores importance if not explicitly set (importance == 5 default)
    - Checks for semantic duplicates beyond exact hash matching

    Returns the memory id if new, None if duplicate (access count bumped).
    """
    global _store_count_this_cycle
    content = content.strip()
    if not content:
        return None

    content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Check for exact duplicate (hash match)
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

    # LLM-powered similarity detection (beyond exact hash)
    if USE_LLM:
        candidates = db.execute(
            "SELECT id, content FROM memories WHERE is_active = 1 "
            "ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        similar_id = check_similar(content, candidates)
        if similar_id is not None:
            log(f"LLM detected similar memory (id={similar_id}), bumping access")
            db.execute(
                "UPDATE memories SET accessed_at=?, access_count=access_count+1 WHERE id=?",
                (now, similar_id)
            )
            return None

    # LLM-powered auto-importance scoring (when no explicit importance given)
    if USE_LLM and importance == 5:
        scored = score_importance(content)
        if scored != 5:
            log(f"LLM scored importance: {scored}")
            importance = scored

    # Set initial difficulty from importance: difficulty = 11 - importance
    initial_difficulty = max(1.0, min(10.0, 11.0 - importance))

    db.execute(
        "INSERT INTO memories(content, importance, category, source, created_at, "
        "accessed_at, access_count, content_hash, stability_days, difficulty, tier) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, ?, 1.0, ?, 'hot')",
        (content, importance, category, source, now, now, content_hash, initial_difficulty)
    )
    mid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    _store_count_this_cycle += 1
    return mid


def supersede(db, old_id, new_id):
    """Mark an old memory as superseded by a new one."""
    db.execute(
        "UPDATE memories SET superseded_by=?, is_active=0 WHERE id=?",
        (new_id, old_id)
    )


# =========================================================================
#  Auto-Supersession (Step 6)
# =========================================================================

ROLLING_PATTERNS = {
    "session_reflection": 5,
    "health_check": 3,
    "morning_ritual": 3,
    "evening_ritual": 3,
}


def check_supersession(db, new_id, content, category, importance):
    """Check if this new memory supersedes an existing one.

    Mechanism 1: Explicit reference ("supersedes memory #NNN")
    Mechanism 2: High Jaccard similarity with same-category memories
    Mechanism 3: Pattern-based rolling windows
    """
    # Mechanism 1: Explicit reference
    match = re.search(r"supersedes?\s+(?:memory\s+)?#?(\d+)", content, re.IGNORECASE)
    if match:
        old_id = int(match.group(1))
        # Verify old_id exists
        exists = db.execute("SELECT id FROM memories WHERE id=?", (old_id,)).fetchone()
        if exists:
            supersede(db, old_id, new_id)
            return old_id

    # Mechanism 2: Jaccard similarity (only for decision/general, importance < 9)
    if category in ("decision", "general") and importance < 9:
        candidates = db.execute("""
            SELECT id, content FROM memories
            WHERE is_active=1 AND category=? AND id != ?
            ORDER BY created_at DESC LIMIT 30
        """, (category, new_id)).fetchall()

        for old_id, old_content in candidates:
            sim = jaccard_similarity(content, old_content)
            if sim >= 0.85:
                supersede(db, old_id, new_id)
                log(f"auto-superseded #{old_id} (jaccard={sim:.2f})")
                return old_id

    # Mechanism 3: Rolling windows for recurring patterns
    content_lower = content.lower()
    for pattern, keep_n in ROLLING_PATTERNS.items():
        if pattern in content_lower:
            old_memories = db.execute("""
                SELECT id FROM memories
                WHERE is_active=1 AND content LIKE ?
                ORDER BY created_at DESC
            """, (f"%{pattern}%",)).fetchall()

            if len(old_memories) > keep_n:
                for excess in old_memories[keep_n:]:
                    supersede(db, excess[0], new_id)
            break

    return None


def resolve_supersession(db, memory_id, max_depth=10):
    """Follow supersession chain to find the current version."""
    current = memory_id
    depth = 0
    while depth < max_depth:
        row = db.execute(
            "SELECT superseded_by FROM memories WHERE id=?", (current,)
        ).fetchone()
        if not row or row[0] is None:
            break
        current = row[0]
        depth += 1
    return current


# =========================================================================
#  Retrieval — five-factor composite scoring with tiered search (Steps 4-5)
# =========================================================================

def search_fts(db, query, limit=10, tier=None, category=None, exclude_ids=None):
    """Low-level FTS5 search with optional tier and category filters.

    Returns list of tuples:
        (id, content, importance, category, source, created_at, accessed_at,
         access_count, rank, stability_days)
    """
    if not query or not query.strip():
        return []

    # Convert multi-word queries to OR syntax for broader matching.
    # FTS5 defaults to AND which misses partial matches.
    words = query.strip().split()
    if len(words) > 1 and "OR" not in query and "AND" not in query and '"' not in query:
        query = " OR ".join(words)

    sql = """
        SELECT m.id, m.content, m.importance, m.category, m.source,
               m.created_at, m.accessed_at, m.access_count, rank,
               m.stability_days
        FROM memories_fts fts
        JOIN memories m ON m.id = fts.rowid
        WHERE memories_fts MATCH ? AND m.is_active = 1
    """
    params = [query]
    if tier:
        sql += " AND m.tier = ?"
        params.append(tier)
    if category:
        sql += " AND m.category = ?"
        params.append(category)
    if exclude_ids:
        placeholders = ",".join("?" for _ in exclude_ids)
        sql += f" AND m.id NOT IN ({placeholders})"
        params.extend(exclude_ids)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit * 3)  # over-fetch for re-ranking

    try:
        return db.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def search(db, query, limit=10, category=None):
    """Search memories using tiered FTS5 + five-factor composite scoring.

    Hot tier is searched first. Cold tier searched only if hot yields
    fewer than limit results.
    """
    global _total_queries
    _total_queries += 1

    if not query or not query.strip():
        return []

    now = datetime.now(timezone.utc)

    # Stage 1: Search hot tier
    hot_rows = search_fts(db, query, limit=limit, tier="hot", category=category)

    # Stage 2: If hot tier didn't fill, search cold tier
    all_rows = list(hot_rows)
    if len(hot_rows) < limit:
        cold_rows = search_fts(db, query, limit=limit, tier="cold", category=category)
        all_rows.extend(cold_rows)

    # If tiered search got nothing (e.g. no tier column yet), fall back to untiered
    if not all_rows:
        all_rows = search_fts(db, query, limit=limit, tier=None, category=category)

    # Re-rank with five-factor composite score
    scored = []
    seen_ids = set()
    for row in all_rows:
        mid = row[0]
        if mid in seen_ids:
            continue
        seen_ids.add(mid)

        score = composite_score(
            bm25_rank=row[8],
            importance=row[2],
            created_at_str=row[5],
            access_count=row[7],
            stability_days=row[9] if len(row) > 9 else 1.0,
            total_queries=_total_queries,
            now=now
        )
        scored.append((score, row))

    scored.sort(key=lambda x: -x[0])

    # Update access via FSRS on-access (Step 3)
    results = []
    for _, row in scored[:limit]:
        on_memory_used(db, row[0], was_relevant=True)
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
#  Consolidation (enhanced with FSRS decay, merge, retier)
# =========================================================================

def fsrs_decay(db, now):
    """Update tier/active status based on FSRS retrievability.

    When R drops below 0.3 and past TTL with low access: deactivate.
    When R drops below 0.5 and past TTL: demote to cold.
    """
    decayed = 0
    rows = db.execute("""
        SELECT id, accessed_at, stability_days, importance, category, access_count
        FROM memories WHERE is_active = 1
    """).fetchall()

    for mid, accessed_at, stability, importance, category, access_count in rows:
        config = CATEGORY_CONFIG.get(category, CATEGORY_CONFIG["general"])
        if config["protected"]:
            continue

        last_access = datetime.fromisoformat(accessed_at.replace("Z", "+00:00"))
        age_since_access = (now - last_access).total_seconds() / 86400.0

        r = 1.0 / (1.0 + age_since_access / (9.0 * max(stability, 0.1)))

        ttl = config.get("ttl_days")
        past_ttl = ttl is not None and age_since_access > ttl

        if r < 0.3 and past_ttl and access_count < 5:
            db.execute("UPDATE memories SET is_active=0, tier='cold' WHERE id=?", (mid,))
            decayed += 1
        elif r < 0.5 and past_ttl:
            db.execute("UPDATE memories SET tier='cold' WHERE id=? AND tier='hot'", (mid,))

    return decayed


def find_merge_candidates(db, category="observation", threshold=0.65, max_age_days=7):
    """Find groups of similar memories to merge.

    Uses Jaccard similarity with union-find grouping.
    Only returns groups of 3+ memories.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()

    rows = db.execute("""
        SELECT id, content FROM memories
        WHERE is_active=1 AND category=? AND created_at > ?
        ORDER BY created_at DESC LIMIT 50
    """, (category, cutoff)).fetchall()

    groups = {}
    group_members = {}
    next_group = 0

    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            sim = jaccard_similarity(rows[i][1], rows[j][1])
            if sim >= threshold:
                gi = groups.get(rows[i][0])
                gj = groups.get(rows[j][0])

                if gi is None and gj is None:
                    groups[rows[i][0]] = next_group
                    groups[rows[j][0]] = next_group
                    group_members[next_group] = [rows[i][0], rows[j][0]]
                    next_group += 1
                elif gi is not None and gj is None:
                    groups[rows[j][0]] = gi
                    group_members[gi].append(rows[j][0])
                elif gi is None and gj is not None:
                    groups[rows[i][0]] = gj
                    group_members[gj].append(rows[i][0])

    return {gid: mids for gid, mids in group_members.items() if len(mids) >= 3}


def merge_group(db, memory_ids, now):
    """Merge a group of similar memories into one.

    With LLM: generates a summary.
    Without LLM: keeps the highest-importance memory, supersedes rest.
    """
    memories = []
    for mid in memory_ids:
        row = db.execute(
            "SELECT id, content, importance, category FROM memories WHERE id=?", (mid,)
        ).fetchone()
        if row:
            memories.append(row)

    if not memories:
        return None

    memories.sort(key=lambda m: m[2], reverse=True)
    survivor_id = memories[0][0]
    max_importance = memories[0][2]

    if USE_LLM:
        contents = "\n".join(f"- {m[1]}" for m in memories)
        summary = _call_small_llm(
            "Merge these related memories into ONE concise memory. "
            "Preserve all unique facts. Remove redundancy. "
            "Respond with ONLY the merged memory text.",
            contents
        )
        if summary:
            content_hash = hashlib.sha256(summary.encode()).hexdigest()[:32]
            now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            db.execute(
                "INSERT INTO memories(content, importance, category, source, "
                "created_at, accessed_at, access_count, content_hash, is_active, "
                "stability_days, difficulty, tier) "
                "VALUES (?, ?, ?, 'consolidation', ?, ?, 0, ?, 1, 1.0, ?, 'hot')",
                (summary, max_importance, memories[0][3], now_str, now_str,
                 content_hash, max(1.0, 11.0 - max_importance))
            )
            survivor_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    for m in memories:
        if m[0] != survivor_id:
            db.execute(
                "UPDATE memories SET superseded_by=?, is_active=0 WHERE id=?",
                (survivor_id, m[0])
            )

    # Log the consolidation
    now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        db.execute(
            "INSERT INTO consolidation_log(operation, source_ids, result_id, summary, created_at) "
            "VALUES ('merge', ?, ?, ?, ?)",
            (json.dumps(memory_ids), survivor_id,
             f"Merged {len(memory_ids)} memories", now_str)
        )
    except sqlite3.OperationalError:
        pass  # consolidation_log table might not exist yet

    return survivor_id


def retier_memories(db, now):
    """Reassign memories to hot/cold tiers based on utility score.

    Hot tier target: ~HOT_TIER_SIZE memories.
    """
    rows = db.execute("""
        SELECT id, importance, accessed_at, stability_days, access_count, category
        FROM memories WHERE is_active = 1
    """).fetchall()

    scored = []
    for mid, imp, accessed_at, stability, access_count, category in rows:
        config = CATEGORY_CONFIG.get(category, CATEGORY_CONFIG["general"])

        if config.get("tier_override") == "hot":
            scored.append((float('inf'), mid))
            continue

        last_access = datetime.fromisoformat(accessed_at.replace("Z", "+00:00"))
        age = (now - last_access).total_seconds() / 86400.0
        r = 1.0 / (1.0 + age / (9.0 * max(stability, 0.1)))

        utility = (imp / 10.0) * r * (1.0 + math.log(access_count + 1))
        scored.append((utility, mid))

    scored.sort(key=lambda x: -x[0])

    hot_ids = {mid for _, mid in scored[:HOT_TIER_SIZE]}
    retiered = 0

    for _, mid in scored:
        new_tier = "hot" if mid in hot_ids else "cold"
        updated = db.execute(
            "UPDATE memories SET tier=? WHERE id=? AND tier!=?",
            (new_tier, mid, new_tier)
        ).rowcount
        retiered += updated

    return retiered


def consolidate(db):
    """Three-phase consolidation. Runs every hippocampus cycle.

    Phase 1: FSRS Stability Decay
    Phase 2: Merge Similar Memories (LLM only, max 5 merges)
    Phase 3: Tier Reassignment & Prune
    """
    now = datetime.now(timezone.utc)

    # Phase 1: FSRS Stability Decay
    decayed = fsrs_decay(db, now)
    if decayed > 0:
        log(f"decayed {decayed} memories via FSRS")

    # Phase 2: Merge Similar Memories (only if LLM available)
    merged = 0
    if USE_LLM:
        for cat in ("observation", "general", "system"):
            groups = find_merge_candidates(db, category=cat)
            for gid, mids in list(groups.items())[:5]:
                result = merge_group(db, mids, now)
                if result:
                    merged += 1
            if merged >= 5:
                break

    # Phase 3: Tier Reassignment
    retiered = retier_memories(db, now)

    # Phase 4: Prune excess inactive memories
    pruned = 0
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
    """Process stimulus lines, storing memories with admission control."""
    if not stimulus_text:
        return 0

    stored = 0
    for line in stimulus_text.splitlines():
        line = line.strip()
        if not line:
            continue

        parsed = parse_remember(line)
        if parsed:
            # Apply admission control (Step 2)
            should_store, importance, category = admit_memory(
                parsed["content"], parsed["importance"], parsed["category"]
            )
            if not should_store:
                continue

            mid = store(db, parsed["content"], importance, category, source="stimulus")
            if mid:
                stored += 1
                # Apply auto-supersession (Step 6)
                check_supersession(db, mid, parsed["content"], category, importance)
                log(f"stored [{category}] imp={importance}: {parsed['content'][:60]}...")

    return stored


# =========================================================================
#  Stats
# =========================================================================

def stats(db):
    """Return memory statistics."""
    total = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    active = db.execute("SELECT COUNT(*) FROM memories WHERE is_active=1").fetchone()[0]
    avg_imp = db.execute(
        "SELECT ROUND(AVG(importance),1) FROM memories WHERE is_active=1"
    ).fetchone()[0] or 0
    categories = db.execute(
        "SELECT category, COUNT(*) FROM memories WHERE is_active=1 "
        "GROUP BY category ORDER BY COUNT(*) DESC"
    ).fetchall()

    # Hot tier count
    try:
        hot_count = db.execute(
            "SELECT COUNT(*) FROM memories WHERE is_active=1 AND tier='hot'"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        hot_count = active  # tier column not yet migrated

    return {
        "total": total,
        "active": active,
        "avg_importance": avg_imp,
        "categories": categories,
        "hot_count": hot_count,
    }


# =========================================================================
#  Main cycle
# =========================================================================

def main():
    global _store_count_this_cycle
    _store_count_this_cycle = 0

    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    init_db(db)

    # Run v2 migrations (idempotent)
    migrate(db)
    backfill_stability(db)

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
