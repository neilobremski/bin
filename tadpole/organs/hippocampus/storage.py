"""Memory storage: admission control, store, stimulus consumption and processing."""
import hashlib
import re
import subprocess
from datetime import datetime, timezone

from config import (
    ADMISSION_RULES, CATEGORY_CONFIG, DEDUP_WINDOW_SECONDS, MAX_STORE_RATE,
    USE_LLM, check_similar, log, score_importance, DIR,
)
from supersession import check_supersession
from entities import extract_and_link_entities

# In-process dedup window
_recent_hashes = {}  # hash -> timestamp
_store_count_this_cycle = 0


def reset_cycle_count():
    """Reset the per-cycle store counter. Called at the start of each main cycle."""
    global _store_count_this_cycle
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
                # Extract entities and build tags (Steps 8-10)
                extract_and_link_entities(db, mid, parsed["content"])
                log(f"stored [{category}] imp={importance}: {parsed['content'][:60]}...")

    return stored
