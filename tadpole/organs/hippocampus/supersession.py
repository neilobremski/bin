"""Auto-supersession: explicit references, Jaccard similarity, rolling windows."""
import re

from config import ROLLING_PATTERNS, log


def jaccard_similarity(text_a, text_b):
    """Word-level Jaccard similarity: |A intersect B| / |A union B|."""
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def supersede(db, old_id, new_id):
    """Mark an old memory as superseded by a new one."""
    db.execute(
        "UPDATE memories SET superseded_by=?, is_active=0 WHERE id=?",
        (new_id, old_id)
    )


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
