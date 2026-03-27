"""Auto-supersession: explicit references and Jaccard similarity."""
import re

from constants import log


def jaccard_similarity(text_a, text_b):
    """Word-level Jaccard similarity: |A & B| / |A | B|."""
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def supersede(db, old_id, new_id):
    """Mark an old memory as superseded by a new one."""
    db.execute(
        "UPDATE memories SET superseded_by=?, is_active=0 WHERE id=?",
        (new_id, old_id)
    )


def check_supersession(db, new_id, content, category, importance):
    """Check if this new memory supersedes an existing one.

    Mechanism 1: Explicit reference ("supersedes memory #NNN")
    Mechanism 2: High Jaccard similarity (>= 0.85) with recent memories
    """
    # Mechanism 1: Explicit reference
    match = re.search(
        r"supersedes?\s+(?:memory\s+)?#?(\d+)", content, re.IGNORECASE)
    if match:
        old_id = int(match.group(1))
        exists = db.execute(
            "SELECT id FROM memories WHERE id=?", (old_id,)
        ).fetchone()
        if exists:
            supersede(db, old_id, new_id)
            return old_id

    # Mechanism 2: Jaccard similarity (skip for critical memories)
    if importance < 9:
        candidates = db.execute("""
            SELECT id, content FROM memories
            WHERE is_active=1 AND id != ?
            ORDER BY created_at DESC LIMIT 30
        """, (new_id,)).fetchall()

        for old_id, old_content in candidates:
            sim = jaccard_similarity(content, old_content)
            if sim >= 0.85:
                supersede(db, old_id, new_id)
                log(f"auto-superseded #{old_id} (jaccard={sim:.2f})")
                return old_id

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
