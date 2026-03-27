"""Entity system: CRUD, extraction, linking, and unconscious recall."""
import json
import sqlite3
from datetime import datetime, timezone

from config import log


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


def list_entities(db):
    """Return all entities with their active link counts.

    Returns list of dicts: {id, name, entity_type, summary, link_count}
    """
    try:
        rows = db.execute("""
            SELECT e.id, e.name, e.entity_type, e.summary,
                   COUNT(em.memory_id) AS link_count
            FROM entities e
            LEFT JOIN entity_memories em
                ON e.id = em.entity_id AND em.valid_until IS NULL
            GROUP BY e.id
            ORDER BY e.name
        """).fetchall()
    except sqlite3.OperationalError:
        return []

    return [
        {"id": r[0], "name": r[1], "entity_type": r[2],
         "summary": r[3], "link_count": r[4]}
        for r in rows
    ]


def get_entity_detail(db, entity_id):
    """Return entity info plus linked memories.

    Returns dict: {id, name, aliases, entity_type, summary, properties,
                   created_at, updated_at, linked_memories}
    or None if not found.

    linked_memories is a list of dicts:
        {id, content, importance, category, created_at}
    """
    try:
        row = db.execute(
            "SELECT id, name, aliases, entity_type, summary, properties, "
            "created_at, updated_at FROM entities WHERE id=?",
            (entity_id,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None

    if not row:
        return None

    linked = []
    try:
        linked_rows = db.execute("""
            SELECT m.id, m.content, m.importance, m.category, m.created_at
            FROM memories m
            JOIN entity_memories em ON m.id = em.memory_id
            WHERE em.entity_id = ? AND em.valid_until IS NULL AND m.is_active = 1
            ORDER BY m.importance DESC, m.created_at DESC LIMIT 20
        """, (entity_id,)).fetchall()
        linked = [
            {"id": r[0], "content": r[1], "importance": r[2],
             "category": r[3], "created_at": r[4]}
            for r in linked_rows
        ]
    except sqlite3.OperationalError:
        pass

    return {
        "id": row[0], "name": row[1], "aliases": row[2],
        "entity_type": row[3], "summary": row[4], "properties": row[5],
        "created_at": row[6], "updated_at": row[7],
        "linked_memories": linked,
    }


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

    Uses a single JOIN query to fetch entities and their top linked memories,
    avoiding the N+1 pattern of one query per entity.
    """
    if not memory_ids:
        return []

    try:
        # Single query: find entities linked to these memories, plus each
        # entity's top 3 linked memories (by importance then recency).
        # The ROW_NUMBER window function limits per-entity memory rows.
        placeholders = ",".join("?" for _ in memory_ids)
        rows = db.execute(f"""
            WITH matched_entities AS (
                SELECT DISTINCT e.id, e.name, e.summary
                FROM entities e
                JOIN entity_memories em ON e.id = em.entity_id
                WHERE em.memory_id IN ({placeholders}) AND em.valid_until IS NULL
            ),
            ranked_memories AS (
                SELECT me.id AS ent_id, m.content, m.importance,
                       ROW_NUMBER() OVER (
                           PARTITION BY me.id
                           ORDER BY m.importance DESC, m.accessed_at DESC
                       ) AS rn
                FROM matched_entities me
                JOIN entity_memories em2 ON me.id = em2.entity_id
                    AND em2.valid_until IS NULL
                JOIN memories m ON m.id = em2.memory_id AND m.is_active = 1
            )
            SELECT me.id, me.name, me.summary,
                   rm.content, rm.importance
            FROM matched_entities me
            LEFT JOIN ranked_memories rm ON me.id = rm.ent_id AND rm.rn <= 3
            ORDER BY me.name, rm.importance DESC
        """, memory_ids).fetchall()
    except sqlite3.OperationalError:
        return []

    # Group rows by entity
    from collections import OrderedDict
    entities_map = OrderedDict()
    for ent_id, name, summary, mem_content, mem_importance in rows:
        if ent_id not in entities_map:
            entities_map[ent_id] = {
                "name": name, "summary": summary, "memories": []
            }
        if mem_content is not None:
            entities_map[ent_id]["memories"].append((mem_content, mem_importance))

    context = []
    for ent_id, info in entities_map.items():
        block = f"[Entity: {info['name']}] {info['summary']}"
        for content, importance in info["memories"]:
            block += f"\n  - [{importance}] {content[:200]}"
        context.append(block)

    return context
