"""Consolidation: FSRS decay, merge similar memories, retier, prune."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone, timedelta

from config import MAX_MEMORIES, USE_LLM, _call_small_llm, log
from supersession import jaccard_similarity
from stability import fsrs_decay, retier_memories


def find_merge_candidates(db, threshold=0.65, max_age_days=7):
    """Find groups of similar memories to merge.

    Uses Jaccard similarity with union-find grouping across ALL active memories.
    Only returns groups of 3+ memories.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()

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
        groups = find_merge_candidates(db)
        for gid, mids in list(groups.items())[:5]:
            result = merge_memories(db, mids, now)
            if result:
                merged += 1

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
