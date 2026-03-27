"""Memory storage: admission control, store, dedup."""
import hashlib
import re
from datetime import datetime, timezone

from constants import (
    ADMISSION_RULES, DEDUP_WINDOW_SECONDS, MAX_STORE_RATE, TS_FMT, log,
)
from supersession import check_supersession
from entities import extract_and_link_entities

# In-process dedup window with eviction
_recent_hashes = {}  # hash -> timestamp
_store_count_this_cycle = 0


def reset_cycle_count():
    global _store_count_this_cycle
    _store_count_this_cycle = 0


def _now_ts():
    return datetime.now(timezone.utc).timestamp()


def _now_str():
    return datetime.now(timezone.utc).strftime(TS_FMT)


def _evict_expired():
    """Sweep _recent_hashes for entries older than DEDUP_WINDOW_SECONDS."""
    now = _now_ts()
    expired = [k for k, v in _recent_hashes.items()
               if now - v > DEDUP_WINDOW_SECONDS]
    for k in expired:
        del _recent_hashes[k]


def admit_memory(content, importance=5, category="general"):
    """Decide whether to store a memory.

    Returns (should_store, adjusted_importance, adjusted_category).
    """
    global _store_count_this_cycle
    content_lower = content.lower().strip()

    if len(content_lower) < 10:
        return False, importance, category

    if _store_count_this_cycle >= MAX_STORE_RATE:
        return False, importance, category

    for pattern, action, imp_override in ADMISSION_RULES:
        if re.search(pattern, content_lower):
            if action == "reject":
                return False, importance, category
            if imp_override and importance == 5:
                importance = imp_override
            break

    # In-process dedup with eviction
    _evict_expired()
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
    now = _now_ts()
    if content_hash in _recent_hashes:
        if now - _recent_hashes[content_hash] < DEDUP_WINDOW_SECONDS:
            return False, importance, category
    _recent_hashes[content_hash] = now

    return True, importance, category


def store(db, content, importance=5, category="general", source=""):
    """Store a memory with dedup by content hash.

    Returns the memory id if new, None if duplicate (access count bumped).
    """
    global _store_count_this_cycle
    content = content.strip()
    if not content:
        return None

    content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
    now = _now_str()

    # Check for exact duplicate
    existing = db.execute(
        "SELECT id, importance FROM memories WHERE content_hash = ?",
        (content_hash,)
    ).fetchone()

    if existing:
        new_imp = max(existing[1], importance)
        db.execute(
            "UPDATE memories SET accessed_at=?, access_count=access_count+1, "
            "importance=? WHERE id=?",
            (now, new_imp, existing[0])
        )
        return None

    initial_difficulty = max(1.0, min(10.0, 11.0 - importance))

    db.execute(
        "INSERT INTO memories(content, importance, category, source, created_at, "
        "accessed_at, access_count, content_hash, stability_days, difficulty, tier) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, ?, 1.0, ?, 'hot')",
        (content, importance, category, source, now, now, content_hash,
         initial_difficulty)
    )
    mid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    _store_count_this_cycle += 1

    extract_and_link_entities(db, mid, content)

    return mid


def store_and_check(db, content, importance=5, category="general", source=""):
    """Store with admission control and supersession. Returns memory id or None."""
    should_store, importance, category = admit_memory(content, importance, category)
    if not should_store:
        return None

    mid = store(db, content, importance, category, source=source)
    if mid:
        check_supersession(db, mid, content, category, importance)
        log(f"stored [{category}] imp={importance}: {content[:60]}")
    return mid
