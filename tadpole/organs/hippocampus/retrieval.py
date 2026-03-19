"""Memory retrieval: FTS5 search, composite scoring, on-access FSRS updates."""
import math
import sqlite3
from datetime import datetime, timezone

from config import log
import config as _config


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


def composite_score(bm25_rank, importance, created_at_str, access_count,
                    stability_days, total_queries, now):
    """Five-factor composite retrieval score.

    All factors are normalized to [0, 1] before weighting.

    Weights: relevance 0.35, importance 0.25, recency 0.15,
             FSRS retrievability 0.15, UCB exploration 0.10
    """
    # Factor 1: Relevance (BM25) -- weight 0.35
    # Log transform preserves ranking differentiation better than sigmoid,
    # which compressed scores into near-binary 0/1 clusters.
    bm25_raw = -bm25_rank  # raw BM25 scores (higher = more relevant)
    relevance = min(1.0, math.log(1.0 + abs(bm25_raw)) / math.log(1.0 + 10.0))

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


def search_fts(db, query, limit=10, tier=None, category=None, exclude_ids=None):
    """Low-level FTS5 search with optional tier and category filters.

    Returns list of tuples:
        (id, content, importance, category, source, created_at, accessed_at,
         access_count, rank, stability_days)
    """
    if not query or not query.strip():
        return []

    # Multi-word strategy: try AND first (precise), fall back to OR (broad).
    # AND prevents false memories ("defendant confessed" won't match "defendant at bank").
    # OR catches partial matches ("phone broken" matches memories with just "phone").
    _original_query = query.strip()

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
        results = db.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        results = []

    # AND-then-OR fallback: if AND returned nothing, retry with OR for partial matches
    if not results:
        words = _original_query.split()
        if len(words) > 1 and "OR" not in _original_query and "AND" not in _original_query:
            or_query = " OR ".join(words)
            return search_fts(db, or_query, limit, tier, category, exclude_ids)

    return results


def search(db, query, limit=10, category=None):
    """Search memories using tiered FTS5 + five-factor composite scoring.

    Hot tier is searched first. Cold tier searched only if hot yields
    fewer than limit results.
    """
    _config._total_queries += 1

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
            total_queries=_config._total_queries,
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
