"""Entity system: CRUD, extraction, linking, and unconscious recall."""
import json
import sqlite3
from datetime import datetime, timezone

from config import USE_LLM, _call_small_llm, log


SEED_ENTITIES = [
    {
        "id": "neil",
        "name": "Neil",
        "aliases": ["Neil", "human", "collaborator", "partner"],
        "entity_type": "person",
        "summary": "Primary human collaborator.",
        "properties": {}
    },
    {
        "id": "tadpole",
        "name": "Tadpole",
        "aliases": ["tadpole", "organism"],
        "entity_type": "organism",
        "summary": "The organism itself — a growing tadpole.",
        "properties": {}
    },
]


def seed_entities(db):
    """Seed initial entities if entities table is empty."""
    try:
        count = db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    except sqlite3.OperationalError:
        return  # table doesn't exist yet
    if count > 0:
        return

    now = datetime.now(timezone.utc).isoformat()
    for ent in SEED_ENTITIES:
        db.execute(
            "INSERT OR IGNORE INTO entities(id, name, aliases, entity_type, "
            "summary, properties, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ent["id"], ent["name"], json.dumps(ent["aliases"]),
             ent["entity_type"], ent["summary"], json.dumps(ent["properties"]),
             now, now)
        )
    db.commit()
    log(f"seeded {len(SEED_ENTITIES)} entities")


def get_entity(db, entity_id):
    """Get a single entity by id. Returns row or None."""
    try:
        return db.execute(
            "SELECT id, name, aliases, entity_type, summary, properties, "
            "created_at, updated_at FROM entities WHERE id=?",
            (entity_id,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None


def create_entity(db, entity_id, name, aliases=None, entity_type="thing",
                  summary="", properties=None):
    """Create a new entity. Returns True if created, False if exists."""
    if aliases is None:
        aliases = [name]
    if properties is None:
        properties = {}

    now = datetime.now(timezone.utc).isoformat()
    try:
        db.execute(
            "INSERT OR IGNORE INTO entities(id, name, aliases, entity_type, "
            "summary, properties, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (entity_id, name, json.dumps(aliases), entity_type,
             summary, json.dumps(properties), now, now)
        )
        return db.execute("SELECT changes()").fetchone()[0] > 0
    except sqlite3.OperationalError:
        return False


def update_entity_summary(db, entity_id):
    """Regenerate entity summary from linked memories. LLM-only."""
    if not USE_LLM:
        return

    try:
        entity = db.execute(
            "SELECT name, summary FROM entities WHERE id=?", (entity_id,)
        ).fetchone()
    except sqlite3.OperationalError:
        return
    if not entity:
        return

    recent_memories = db.execute("""
        SELECT m.content FROM memories m
        JOIN entity_memories em ON m.id = em.memory_id
        WHERE em.entity_id = ? AND m.is_active = 1
        ORDER BY m.created_at DESC LIMIT 10
    """, (entity_id,)).fetchall()

    if len(recent_memories) < 3:
        return  # not enough data

    memories_text = "\n".join(f"- {m[0][:200]}" for m in recent_memories)
    new_summary = _call_small_llm(
        f"Summarize everything known about '{entity[0]}' from these memories. "
        "Be concise (2-3 sentences). Include key facts and current status.",
        memories_text
    )

    if new_summary:
        db.execute(
            "UPDATE entities SET summary=?, updated_at=? WHERE id=?",
            (new_summary, datetime.now(timezone.utc).isoformat(), entity_id)
        )


def extract_and_link_entities(db, memory_id, content):
    """Scan memory content for known entity aliases, create links and tags.

    Fast path: string matching against known entity names (no LLM).
    Also builds pipe-delimited tags on the memory row.
    """
    try:
        entities = db.execute("SELECT id, aliases FROM entities").fetchall()
    except sqlite3.OperationalError:
        return  # entities table doesn't exist yet

    now = datetime.now(timezone.utc).isoformat()
    content_lower = content.lower()
    matched_entity_ids = []

    for ent_id, aliases_json in entities:
        aliases = json.loads(aliases_json)
        for alias in aliases:
            if alias.lower() in content_lower:
                # Upsert entity_memory link
                try:
                    db.execute("""
                        INSERT OR IGNORE INTO entity_memories(entity_id, memory_id, relationship, valid_from)
                        VALUES (?, ?, 'mentions', ?)
                    """, (ent_id, memory_id, now))
                except sqlite3.OperationalError:
                    pass
                matched_entity_ids.append(ent_id)
                break  # one link per entity per memory

    # Build tags from entity links (pipe-delimited with bookends)
    if matched_entity_ids:
        tags = "|" + "|".join(matched_entity_ids) + "|"
        try:
            db.execute("UPDATE memories SET tags=? WHERE id=?", (tags, memory_id))
        except sqlite3.OperationalError:
            pass  # tags column might not exist yet


def get_entity_context(db, memory_ids):
    """Given a list of memory IDs, return entity context for any linked entities.

    Returns list of strings like "[Entity: Neil] Primary human collaborator."
    """
    if not memory_ids:
        return []

    try:
        # Find all entities linked to these memories
        placeholders = ",".join("?" for _ in memory_ids)
        rows = db.execute(f"""
            SELECT DISTINCT e.id, e.name, e.summary
            FROM entities e
            JOIN entity_memories em ON e.id = em.entity_id
            WHERE em.memory_id IN ({placeholders}) AND em.valid_until IS NULL
        """, memory_ids).fetchall()
    except sqlite3.OperationalError:
        return []

    context = []
    for ent_id, name, summary in rows:
        block = f"[Entity: {name}] {summary}"

        # Get top linked memories for this entity
        linked = db.execute("""
            SELECT m.content, m.importance FROM memories m
            JOIN entity_memories em ON m.id = em.memory_id
            WHERE em.entity_id = ? AND em.valid_until IS NULL AND m.is_active = 1
            ORDER BY m.importance DESC, m.accessed_at DESC
            LIMIT 3
        """, (ent_id,)).fetchall()

        for content, importance in linked:
            block += f"\n  - [{importance}] {content[:200]}"

        context.append(block)

    return context


def unconscious_entity_recall(db, message_text, max_entities=3, memories_per_entity=3):
    """Scan message for entity aliases. For each match, return entity summary
    and top linked memories.

    This is the UNCONSCIOUS memory path — auto-injected, no explicit search needed.
    """
    try:
        entities = db.execute(
            "SELECT id, name, aliases, summary FROM entities"
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    message_lower = message_text.lower()

    triggered = []
    for ent_id, name, aliases_json, summary in entities:
        aliases = json.loads(aliases_json)
        for alias in aliases:
            if alias.lower() in message_lower:
                triggered.append((ent_id, name, summary))
                break

    if not triggered:
        return []

    context_blocks = []
    for ent_id, name, summary in triggered[:max_entities]:
        block = f"[Entity: {name}] {summary}"

        try:
            linked = db.execute("""
                SELECT m.content, m.importance FROM memories m
                JOIN entity_memories em ON m.id = em.memory_id
                WHERE em.entity_id = ? AND em.valid_until IS NULL AND m.is_active = 1
                ORDER BY m.importance DESC, m.accessed_at DESC
                LIMIT ?
            """, (ent_id, memories_per_entity)).fetchall()
        except sqlite3.OperationalError:
            linked = []

        for content, importance in linked:
            block += f"\n  - [{importance}] {content[:200]}"

        context_blocks.append(block)

    return context_blocks
