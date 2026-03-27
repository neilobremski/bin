"""Entity system: CRUD, extraction, linking. No hardcoded seeds."""
import json
import sqlite3
from datetime import datetime, timezone

from constants import TS_FMT, log


def list_entities(db):
    """Return all entities with active link counts."""
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
    """Return entity info plus linked memories, or None if not found."""
    try:
        row = db.execute(
            "SELECT id, name, aliases, entity_type, summary, "
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
        "entity_type": row[3], "summary": row[4],
        "created_at": row[5], "updated_at": row[6],
        "linked_memories": linked,
    }


def create_entity(db, entity_id, name, aliases=None, entity_type="thing",
                  summary=""):
    """Create a new entity. Returns True if created, False if exists."""
    now = datetime.now(timezone.utc).strftime(TS_FMT)
    aliases = aliases or [name]
    try:
        db.execute(
            "INSERT INTO entities(id, name, aliases, entity_type, summary, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (entity_id, name, json.dumps(aliases), entity_type, summary,
             now, now)
        )
        return True
    except sqlite3.IntegrityError:
        return False


def extract_and_link_entities(db, memory_id, content):
    """Scan content for known entity aliases, create links and tags."""
    try:
        entities = db.execute("SELECT id, aliases FROM entities").fetchall()
    except sqlite3.OperationalError:
        return

    now = datetime.now(timezone.utc).strftime(TS_FMT)
    content_lower = content.lower()
    matched = []

    for ent_id, aliases_json in entities:
        aliases = json.loads(aliases_json)
        for alias in aliases:
            if alias.lower() in content_lower:
                try:
                    db.execute(
                        "INSERT OR IGNORE INTO entity_memories"
                        "(entity_id, memory_id, relationship, valid_from) "
                        "VALUES (?, ?, 'mentions', ?)",
                        (ent_id, memory_id, now)
                    )
                except sqlite3.OperationalError:
                    pass
                matched.append(ent_id)
                break

    if matched:
        tags = "|" + "|".join(matched) + "|"
        try:
            db.execute("UPDATE memories SET tags=? WHERE id=?",
                       (tags, memory_id))
        except sqlite3.OperationalError:
            pass


def get_entity_context(db, memory_ids):
    """Given memory IDs, return entity context strings for linked entities."""
    if not memory_ids:
        return []

    try:
        placeholders = ",".join("?" for _ in memory_ids)
        rows = db.execute(f"""
            WITH matched_entities AS (
                SELECT DISTINCT e.id, e.name, e.summary
                FROM entities e
                JOIN entity_memories em ON e.id = em.entity_id
                WHERE em.memory_id IN ({placeholders})
                  AND em.valid_until IS NULL
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

    from collections import OrderedDict
    entities_map = OrderedDict()
    for ent_id, name, summary, mem_content, mem_importance in rows:
        if ent_id not in entities_map:
            entities_map[ent_id] = {"name": name, "summary": summary,
                                   "memories": []}
        if mem_content is not None:
            entities_map[ent_id]["memories"].append(
                (mem_content, mem_importance))

    context = []
    for ent_id, info in entities_map.items():
        block = f"[Entity: {info['name']}] {info['summary']}"
        for content, importance in info["memories"]:
            block += f"\n  - [{importance}] {content[:200]}"
        context.append(block)

    return context
