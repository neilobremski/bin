"""Memory retrieval: FTS5 search, composite scoring, on-access FSRS updates."""
import math
import sqlite3
from datetime import datetime, timezone

from constants import TS_FMT, log

# Session query counter for UCB exploration (module-scoped, not in config)
_total_queries = 0


def on_memory_used(db, memory_id, was_relevant):
    """FSRS-inspired stability update on memory access.

    If relevant:  S_new = S * (1.0 + gain_factor)
    If irrelevant: S_new = S * 0.9
    """
    now = datetime.now(timezone.utc)
    row = db.execute(
        "SELECT accessed_at, stability_days, difficulty, importance, access_count "
        "FROM memories WHERE id=?", (memory_id,)
    ).fetchone()

    if not row:
        return

    accessed_at_str, stability, difficulty, importance, access_count = row
    last_access = datetime.strptime(accessed_at_str, TS_FMT).replace(
        tzinfo=timezone.utc)
    elapsed_days = max((now - last_access).total_seconds() / 86400.0, 0.01)

    r = 1.0 / (1.0 + elapsed_days / (9.0 * max(stability, 0.1)))
    now_str = now.strftime(TS_FMT)

    if was_relevant:
        difficulty_factor = (11.0 - difficulty) / 10.0
        retrievability_bonus = math.exp(0.5 * (1.0 - r)) - 1.0
        gain = 0.1 + 0.3 * difficulty_factor * retrievability_bonus
        if access_count > 20:
            gain *= 0.5
        new_stability = min(365.0, stability * (1.0 + gain))
        new_difficulty = max(1.0, difficulty - 0.1)
    else:
        new_stability = max(0.5, stability * 0.9)
        new_difficulty = min(10.0, difficulty + 0.1)

    db.execute(
        "UPDATE memories SET stability_days=?, difficulty=?, accessed_at=?, "
        "access_count=access_count+1 WHERE id=?",
        (new_stability, new_difficulty, now_str, memory_id)
    )


def composite_score(bm25_score, importance, age_days, access_count,
                    stability_days, total_queries):
    """Five-factor composite retrieval score. All factors in [0, 1].

    Weights: relevance 0.35, importance 0.25, recency 0.15,
             FSRS retrievability 0.15, UCB exploration 0.10

    bm25_score: raw FTS5 BM25 value (negative float, more negative = better).
                Negated to positive, then log-normalized against an assumed
                max of 10.0. Scores above 10 are clamped to 1.0.
    """
    # Factor 1: Relevance (BM25 raw score, not rank position)
    raw = -bm25_score  # convert to positive (higher = more relevant)
    relevance = min(1.0, math.log(1.0 + abs(raw)) / math.log(1.0 + 10.0))

    # Factor 2: Importance
    imp = min(1.0, max(0.0, importance / 10.0))

    # Factor 3: Recency
    recency = 1.0 / (1.0 + math.log(1.0 + max(age_days, 0.01)))

    # Factor 4: FSRS retrievability
    r = 1.0 / (1.0 + max(age_days, 0.01) / (9.0 * max(stability_days, 0.1)))

    # Factor 5: UCB exploration bonus
    exploration = math.sqrt(
        2.0 * math.log(total_queries + 1) / (access_count + 1))
    exploration = min(1.0, exploration)

    return (0.35 * relevance + 0.25 * imp + 0.15 * recency +
            0.15 * r + 0.10 * exploration)


def search_fts(db, query, limit=10, tier=None, category=None, exclude_ids=None):
    """Low-level FTS5 search with optional filters.

    Returns list of tuples:
        (id, content, importance, category, source, created_at, accessed_at,
         access_count, bm25_score, stability_days, age_days)
    """
    if not query or not query.strip():
        return []

    original = query.strip()

    sql = """
        SELECT m.id, m.content, m.importance, m.category, m.source,
               m.created_at, m.accessed_at, m.access_count, rank,
               m.stability_days,
               MAX(0.01, julianday('now') - julianday(m.created_at)) AS age_days
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
    params.append(limit * 3)

    try:
        results = db.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        results = []

    # AND-then-OR fallback for multi-word queries
    if not results:
        words = original.split()
        if len(words) > 1 and "OR" not in original and "AND" not in original:
            or_query = " OR ".join(words)
            return search_fts(db, or_query, limit, tier, category, exclude_ids)

    return results


def search(db, query, limit=10, category=None):
    """Search memories using tiered FTS5 + five-factor composite scoring.

    Does NOT call db.commit() -- caller manages transactions.
    """
    global _total_queries
    _total_queries += 1

    if not query or not query.strip():
        return []

    # Stage 1: hot tier
    hot_rows = search_fts(db, query, limit=limit, tier="hot", category=category)
    all_rows = list(hot_rows)

    # Stage 2: cold tier if hot didn't fill
    if len(hot_rows) < limit:
        cold_rows = search_fts(db, query, limit=limit, tier="cold",
                               category=category)
        all_rows.extend(cold_rows)

    # Fallback: untiered
    if not all_rows:
        all_rows = search_fts(db, query, limit=limit, tier=None,
                              category=category)

    # Re-rank with composite score
    scored = []
    seen_ids = set()
    for row in all_rows:
        mid = row[0]
        if mid in seen_ids:
            continue
        seen_ids.add(mid)
        score = composite_score(
            bm25_score=row[8],
            importance=row[2],
            age_days=row[10] if len(row) > 10 else 1.0,
            access_count=row[7],
            stability_days=row[9] if len(row) > 9 else 1.0,
            total_queries=_total_queries,
        )
        scored.append((score, row))

    scored.sort(key=lambda x: -x[0])

    # FSRS on-access update (no commit here -- caller commits)
    results = []
    for _, row in scored[:limit]:
        on_memory_used(db, row[0], was_relevant=True)
        results.append(row)

    return results


def recent(db, limit=10, category=None):
    """Get most recent active memories."""
    sql = ("SELECT id, content, importance, category, source, created_at, "
           "accessed_at, access_count FROM memories WHERE is_active=1")
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
        "SELECT id, content, importance, category, source, created_at "
        "FROM memories WHERE is_active=1 AND importance>=? "
        "ORDER BY importance DESC, created_at DESC LIMIT ?",
        (min_importance, limit)
    ).fetchall()


def stats(db):
    """Return memory statistics."""
    total = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    active = db.execute(
        "SELECT COUNT(*) FROM memories WHERE is_active=1").fetchone()[0]
    avg_imp = db.execute(
        "SELECT ROUND(AVG(importance),1) FROM memories WHERE is_active=1"
    ).fetchone()[0] or 0
    categories = db.execute(
        "SELECT category, COUNT(*) FROM memories WHERE is_active=1 "
        "GROUP BY category ORDER BY COUNT(*) DESC"
    ).fetchall()

    try:
        hot_count = db.execute(
            "SELECT COUNT(*) FROM memories WHERE is_active=1 AND tier='hot'"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        hot_count = active

    return {
        "total": total,
        "active": active,
        "avg_importance": avg_imp,
        "categories": categories,
        "hot_count": hot_count,
    }
