"""Consolidation: FSRS decay, merge similar memories, retier, prune."""
import hashlib
from datetime import datetime, timezone, timedelta

from constants import MAX_MEMORIES, TS_FMT, log
from supersession import jaccard_similarity
from stability import fsrs_decay, retier_memories


def find_merge_candidates(db, threshold=0.65, max_age_days=7):
    """Find groups of similar memories to merge (Jaccard, 3+ per group).

    Scans at most 50 recent memories (O(n^2) bounded to 1225 comparisons).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)
              ).strftime(TS_FMT)

    rows = db.execute("""
        SELECT id, content FROM memories
        WHERE is_active=1 AND created_at > ?
        ORDER BY created_at DESC LIMIT 50
    """, (cutoff,)).fetchall()

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


def merge_memories(db, memory_ids, now):
    """Merge a group of similar memories. Keeps highest-importance, supersedes rest."""
    memories = []
    for mid in memory_ids:
        row = db.execute(
            "SELECT id, content, importance, category FROM memories WHERE id=?",
            (mid,)
        ).fetchone()
        if row:
            memories.append(row)

    if not memories:
        return None

    memories.sort(key=lambda m: m[2], reverse=True)
    survivor_id = memories[0][0]

    for m in memories:
        if m[0] != survivor_id:
            db.execute(
                "UPDATE memories SET superseded_by=?, is_active=0 WHERE id=?",
                (survivor_id, m[0])
            )

    return survivor_id


def consolidate(db):
    """Three-phase consolidation. Runs every hippocampus cycle.

    1. FSRS Stability Decay
    2. Merge Similar Memories (max 5 merges)
    3. Tier Reassignment & Prune
    """
    now = datetime.now(timezone.utc)

    # Phase 1: FSRS decay
    decayed = fsrs_decay(db, now)
    if decayed > 0:
        log(f"decayed {decayed} memories via FSRS")

    # Phase 2: Merge similar
    merged = 0
    groups = find_merge_candidates(db)
    for gid, mids in list(groups.items())[:5]:
        result = merge_memories(db, mids, now)
        if result:
            merged += 1

    # Phase 3: Retier
    retier_memories(db, now)

    # Phase 4: Prune excess inactive
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
