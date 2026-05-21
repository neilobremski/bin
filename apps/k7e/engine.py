"""k7e engine — store, search, append, reindex, assets.

Flat markdown files are source of truth. SQLite FTS5 + optional embeddings
are derived indexes, rebuildable from files via reindex().

Binary assets stored content-addressed (SHA256 hash + extension).
Same content = same hash = one file.

Zero non-stdlib dependencies. Embeddings use ollama HTTP API (urllib).
Configurable root via K7E_HOME env var (defaults to ~/.k7e).
"""

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _k7e_home():
    override = os.environ.get("K7E_HOME")
    return Path(override) if override else Path.home() / ".k7e"


NODES_DIR = None
MOCS_DIR = None
ASSETS_DIR = None
INDEX_DB = None

def _ollama_url():
    return os.environ.get("OLLAMA_URL") or _load_config_val("ollama_url", "http://localhost:11434")

def _embed_model():
    return os.environ.get("EMBED_MODEL") or _load_config_val("embed_model", "nomic-embed-text")

def _load_config_val(key, default):
    try:
        import config
        return config.get(key, default)
    except ImportError:
        return default

RRF_K = 60


def reset(home=None):
    """Reset store paths. For testing or multi-store usage."""
    global NODES_DIR, MOCS_DIR, ASSETS_DIR, INDEX_DB
    h = Path(home) if home else _k7e_home()
    NODES_DIR = h / "nodes"
    MOCS_DIR = h / "mocs"
    ASSETS_DIR = h / "assets"
    INDEX_DB = h / ".index.db"


def init():
    global NODES_DIR, MOCS_DIR, ASSETS_DIR, INDEX_DB
    if NODES_DIR is None:
        home = _k7e_home()
        NODES_DIR = home / "nodes"
        MOCS_DIR = home / "mocs"
        ASSETS_DIR = home / "assets"
        INDEX_DB = home / ".index.db"
    NODES_DIR.mkdir(parents=True, exist_ok=True)
    MOCS_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    conn = _connect()
    conn.executescript(_SCHEMA)
    _migrate(conn)
    conn.close()


def next_id():
    """Generate next K7E-BBB-NNNNN ID. Sequential across all buckets.
    Uses a counter in the sqlite meta table for O(1) performance.
    Falls back to filesystem scan once to initialize if counter is missing."""
    conn = _connect()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'next_id_counter'"
    ).fetchone()

    if row is not None:
        total = int(row[0]) + 1
    else:
        # Initialize from filesystem scan (one-time fallback)
        highest = 0
        for bucket_dir in sorted(NODES_DIR.iterdir()):
            if not bucket_dir.is_dir():
                continue
            for f in bucket_dir.glob("K7E-*.md"):
                parts = f.stem.split("-")
                if len(parts) == 3:
                    try:
                        num = int(parts[1]) * 100000 + int(parts[2])
                        highest = max(highest, num)
                    except ValueError:
                        pass
        total = highest + 1

    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('next_id_counter', ?)",
        (str(total),)
    )
    conn.commit()
    conn.close()

    bucket = total // 100000
    seq = total % 100000
    return f"K7E-{bucket:03d}-{seq:05d}"


def _node_path(node_id):
    """Resolve node ID to file path: nodes/BBB/K7E-BBB-NNNNN.md"""
    parts = node_id.split("-")
    if len(parts) == 3:
        bucket = parts[1]
    else:
        bucket = "000"
    return NODES_DIR / bucket / f"{node_id}.md"


def _all_node_files():
    """Iterate all node files across all buckets."""
    for bucket_dir in sorted(NODES_DIR.iterdir()):
        if not bucket_dir.is_dir():
            continue
        for f in sorted(bucket_dir.glob("K7E-*.md")):
            yield f


def store_entry(title, content, tags=None, aliases=None, importance=5):
    """Store a new knowledge entry. Deduplicates by content hash at storage layer.
    For semantic dedup-aware ingestion, use distill."""
    tags = tags or []
    aliases = aliases or []
    init()

    # Content-hash dedup: check for exact duplicate before writing
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
    conn = _connect()
    existing = conn.execute(
        "SELECT id FROM nodes WHERE content_hash = ?", (content_hash,)
    ).fetchone()
    conn.close()
    if existing:
        return existing[0]

    node_id = next_id()
    now = time.strftime("%Y-%m-%d")
    confidence = round(importance / 10, 1)

    body = f"""---
id: {node_id}
title: {title}
aliases: [{', '.join(aliases)}]
status: active
confidence: {confidence}
verification_count: 0
last_updated: {now}
tags: [{', '.join(tags)}]
---

## Verified Protocol

{content.strip()}

## Edge Cases

## False Paths

## History
* {now}: Initial entry.
"""

    node_path = _node_path(node_id)
    node_path.parent.mkdir(parents=True, exist_ok=True)
    node_path.write_text(body, encoding="utf-8")

    _index_node(node_id, title, aliases, tags, content, now, content_hash=content_hash, confidence=confidence)
    _update_mocs(node_id, title, tags)

    return node_id


def append_entry(node_id, section, content):
    node_path = _node_path(node_id)
    if not node_path.exists():
        raise FileNotFoundError(f"Node {node_id} not found")

    text = node_path.read_text(encoding="utf-8")
    now = time.strftime("%Y-%m-%d")

    section_header = f"## {section}"
    if section_header in text:
        parts = text.split(section_header)
        before = parts[0]
        after = parts[1]
        next_section = re.search(r"\n## ", after)
        if next_section:
            section_body = after[:next_section.start()]
            remainder = after[next_section.start():]
        else:
            section_body = after
            remainder = ""
        section_body = section_body.rstrip() + f"\n* {content.strip()}\n"
        text = before + section_header + section_body + remainder
    else:
        text = text.rstrip() + f"\n\n{section_header}\n* {content.strip()}\n"

    # Update last_updated in frontmatter
    text = re.sub(r"last_updated: .+", f"last_updated: {now}", text)

    # Bump verification_count
    match = re.search(r"verification_count: (\d+)", text)
    if match:
        count = int(match.group(1)) + 1
        text = re.sub(r"verification_count: \d+", f"verification_count: {count}", text)

    node_path.write_text(text, encoding="utf-8")

    meta = _parse_frontmatter(text)
    full_content = _extract_body(text)
    _index_node(
        node_id, meta.get("title", ""),
        meta.get("aliases", []), meta.get("tags", []),
        full_content, now
    )

    return node_id


def supersede(old_id, new_id):
    """Mark old_id as superseded by new_id."""
    node_path = _node_path(old_id)
    if not node_path.exists():
        return
    text = node_path.read_text(encoding="utf-8")
    text = re.sub(r"status: active", "status: superseded", text)
    text = re.sub(r"(tags: \[.*?\])", r"\1\nsuperseded_by: " + new_id, text)
    node_path.write_text(text, encoding="utf-8")
    # Update index
    conn = _connect()
    conn.execute("UPDATE nodes SET status = 'superseded', superseded_by = ? WHERE id = ?", (new_id, old_id))
    conn.commit()
    conn.close()


def search(query, limit=5, json_output=False):
    init()
    conn = _connect()

    bm25_results = _search_bm25(conn, query, limit * 3)
    meta_results = _search_metadata(conn, query, limit * 3)
    embed_results = _search_embeddings(conn, query, limit * 3)

    fused = _rrf_fuse([bm25_results, meta_results, embed_results], limit)
    conn.close()

    # Filter out noise: require minimum RRF score.
    # rank-0 in one track = 1/(60+1) ≈ 0.0164
    # rank-0 in two tracks = 2/(60+1) ≈ 0.0328
    # We accept rank-0 single-track hits (0.0164) but reject lower.
    min_score = 1.0 / (RRF_K + 1) - 0.001  # ~0.0154
    fused = [r for r in fused if r["score"] >= min_score]

    # Apply confidence as a tiebreaker boost
    if fused:
        conn2 = _connect()
        for r in fused:
            conf_row = conn2.execute("SELECT confidence FROM nodes WHERE id = ?", (r["id"],)).fetchone()
            if conf_row and conf_row[0]:
                r["score"] = round(r["score"] * (0.7 + 0.3 * conf_row[0]), 4)
        conn2.close()
        fused.sort(key=lambda x: -x["score"])

    if json_output:
        return fused
    return fused


def get(node_id):
    node_path = _node_path(node_id)
    if not node_path.exists():
        raise FileNotFoundError(f"Node {node_id} not found")
    return node_path.read_text(encoding="utf-8")


def reindex(embeddings=False):
    init()
    conn = _connect()
    conn.execute("DELETE FROM nodes")
    conn.execute("DELETE FROM nodes_fts")
    conn.execute("DELETE FROM pending_embeddings")
    if embeddings:
        conn.execute("DELETE FROM embeddings")
    conn.commit()

    for path in _all_node_files():
        text = path.read_text(encoding="utf-8")
        meta = _parse_frontmatter(text)
        body = _extract_body(text)
        node_id = meta.get("id", path.stem)
        title = meta.get("title", "")
        aliases = meta.get("aliases", [])
        tags = meta.get("tags", [])
        now = meta.get("last_updated", time.strftime("%Y-%m-%d"))
        content_hash = hashlib.sha256(body.encode()).hexdigest()[:16]

        conn.execute(
            "INSERT OR REPLACE INTO nodes (id, title, aliases, status, confidence, "
            "verification_count, last_updated, tags, created_at, updated_at, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (node_id, title, ", ".join(aliases), meta.get("status", "active"),
             meta.get("confidence", 0.5), meta.get("verification_count", 0),
             now, ", ".join(tags), now, now, content_hash)
        )
        conn.execute(
            "INSERT INTO nodes_fts (rowid, title, aliases, tags, content) "
            "VALUES ((SELECT rowid FROM nodes WHERE id = ?), ?, ?, ?, ?)",
            (node_id, title, " ".join(aliases), " ".join(tags), body)
        )

        if embeddings:
            vec = embed_text(f"{title} {body[:500]}")
            if vec:
                conn.execute(
                    "INSERT OR REPLACE INTO embeddings (node_id, vector, model, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (node_id, _pack_vector(vec), _embed_model(), now)
                )
            else:
                # Queue for later if embedding service unavailable
                conn.execute(
                    "INSERT OR REPLACE INTO pending_embeddings (node_id, queued_at) VALUES (?, ?)",
                    (node_id, now)
                )

    conn.commit()
    conn.close()

    # Process any pending embeddings queued during reindex
    if embeddings:
        process_pending_embeddings()


def list_nodes(status=None, tag=None):
    init()
    conn = _connect()
    query = "SELECT id, title, status, confidence, tags FROM nodes"
    conditions = []
    params = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if tag:
        conditions.append("tags LIKE ?")
        params.append(f"%{tag}%")
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY last_updated DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "status": r[2], "confidence": r[3], "tags": r[4]} for r in rows]


def rebuild_mocs():
    """Rebuild all MOC files from node tags. Destructive — replaces existing MOCs."""
    init()
    mocs = {}

    for path in _all_node_files():
        text = path.read_text(encoding="utf-8")
        meta = _parse_frontmatter(text)
        node_id = meta.get("id", path.stem)
        title = meta.get("title", "Unknown")
        status = meta.get("status", "active")
        tags = meta.get("tags", [])
        for tag in tags:
            mocs.setdefault(tag, []).append((node_id, title, status))

    for path in MOCS_DIR.glob("*.md"):
        path.unlink()

    for tag, nodes in sorted(mocs.items()):
        active = [(nid, t) for nid, t, s in nodes if s == "active"]
        other = [(nid, t, s) for nid, t, s in nodes if s != "active"]
        content = f"# {tag}\n\n"
        if active:
            content += "## Active\n"
            for nid, title in active:
                content += f"* [[{nid}]] — {title}\n"
            content += "\n"
        if other:
            content += "## Archived\n"
            for nid, title, status in other:
                content += f"* [[{nid}]] — {title} ({status})\n"
            content += "\n"
        (MOCS_DIR / f"{tag}.md").write_text(content, encoding="utf-8")


def stats():
    """Return store statistics."""
    init()
    conn = _connect()
    total_nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    avg_conf = conn.execute("SELECT AVG(confidence) FROM nodes").fetchone()[0] or 0.0
    all_tags = conn.execute("SELECT tags FROM nodes").fetchall()
    conn.close()

    tag_freq = {}
    for row in all_tags:
        if row[0]:
            for t in (t.strip() for t in row[0].split(",") if t.strip()):
                tag_freq[t] = tag_freq.get(t, 0) + 1

    return {
        "total_nodes": total_nodes,
        "total_mocs": len(list(MOCS_DIR.glob("*.md"))),
        "total_assets": len([f for f in ASSETS_DIR.rglob("*.*") if f.name != ".gitkeep"]),
        "avg_confidence": round(avg_conf, 2),
        "top_tags": sorted(tag_freq.items(), key=lambda x: -x[1])[:10],
    }


# --- Compile (knowledge compounding) ---

def compile_tag(tag, dry_run=False):
    """Synthesize all active entries for a tag into a single reference page.

    Requires 3+ active nodes with the given tag. Uses configured LLM to
    produce a compiled overview. Source nodes are NOT modified or deleted.
    Returns the new compiled node ID, or None if dry_run.
    """
    import config
    import subprocess

    init()
    nodes = list_nodes(tag=tag, status="active")
    if len(nodes) < 3:
        print(f"Need 3+ active nodes with tag '{tag}', found {len(nodes)}.", file=sys.stderr)
        return None

    # Gather content from source nodes
    entries = []
    for n in nodes:
        try:
            text = get(n["id"])
            body = _extract_body(text)
            entries.append({"id": n["id"], "title": n["title"], "content": body.strip()})
        except FileNotFoundError:
            continue

    if len(entries) < 3:
        print(f"Need 3+ readable nodes with tag '{tag}', found {len(entries)}.", file=sys.stderr)
        return None

    # Build prompt
    entry_texts = "\n\n---\n\n".join(
        f"[Entry {e['id']}] {e['title']}\n{e['content']}" for e in entries
    )
    prompt = (
        f"Synthesize these {len(entries)} knowledge entries about '{tag}' into a single "
        f"authoritative reference. Include sections: Overview, Procedures, Gotchas, "
        f"Open Questions. Preserve specific technical details (commands, flags, ports). "
        f"Cite source entry IDs.\n\n{entry_texts}"
    )

    if dry_run:
        print(f"Would compile {len(entries)} entries for tag '{tag}':")
        for e in entries:
            print(f"  {e['id']}  {e['title']}")
        return None

    # Call LLM
    cmd = config.resolve_llm_command(prompt)
    compiled_content = None

    if cmd:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
                cwd=os.getcwd(),
            )
            if result.returncode == 0 and result.stdout.strip():
                compiled_content = result.stdout.strip()
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Fallback: ollama HTTP API
    if not compiled_content:
        ollama_url = config.get("ollama_url", "http://localhost:11434")
        model = config.get("llm_model", "qwen3.5:latest")
        try:
            data = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
            req = urllib.request.Request(
                f"{ollama_url}/api/generate",
                data=data, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                response = json.loads(resp.read())
                compiled_content = response.get("response", "").strip()
        except Exception:
            pass

    if not compiled_content:
        print("Error: No LLM available to compile entries. Configure with: k7e config llm <provider>", file=sys.stderr)
        return None

    # Store as a new compiled node
    source_ids = [e["id"] for e in entries]
    content_with_sources = (
        f"{compiled_content}\n\n"
        f"## Sources\n"
        + "\n".join(f"* [[{sid}]]" for sid in source_ids)
    )

    tags_list = [tag, "compiled"]
    node_id = next_id()
    now = time.strftime("%Y-%m-%d")

    body = f"""---
id: {node_id}
title: {tag} — Compiled Reference
aliases: []
status: compiled
confidence: 0.8
verification_count: 0
last_updated: {now}
tags: [{', '.join(tags_list)}]
---

{content_with_sources}

## History
* {now}: Compiled from {len(entries)} entries.
"""

    node_path = _node_path(node_id)
    node_path.parent.mkdir(parents=True, exist_ok=True)
    node_path.write_text(body, encoding="utf-8")

    _index_node(node_id, f"{tag} — Compiled Reference", [], tags_list, content_with_sources, now)
    _update_mocs(node_id, f"{tag} — Compiled Reference", tags_list)

    return node_id


# --- Assets ---

def store_asset(source_path):
    """Content-addressed asset storage. Returns relative path for markdown embedding.
    Same file content = same hash = one copy. Safe to call multiple times."""
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"Asset source not found: {source_path}")

    # Hash the file content
    h = hashlib.sha256()
    with open(source, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    content_hash = h.hexdigest()[:12]
    ext = source.suffix.lower()
    asset_name = f"{content_hash}{ext}"
    # Bucket by first 2 chars of hash (256 buckets, same as git objects)
    bucket = content_hash[:2]
    bucket_dir = ASSETS_DIR / bucket
    bucket_dir.mkdir(parents=True, exist_ok=True)
    dest = bucket_dir / asset_name

    if not dest.exists():
        shutil.copy2(source, dest)

    return f"assets/{bucket}/{asset_name}"


# --- Embedding ---

def embed_text(text):
    try:
        data = json.dumps({"model": _embed_model(), "input": text}).encode()
        req = urllib.request.Request(
            f"{_ollama_url()}/api/embed",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            embeddings = result.get("embeddings", [])
            if embeddings:
                return embeddings[0]
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError):
        pass
    return None


def cosine_similarity(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# --- Internal ---

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    aliases TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    confidence REAL DEFAULT 0.5,
    verification_count INTEGER DEFAULT 0,
    last_updated TEXT,
    tags TEXT DEFAULT '',
    created_at TEXT,
    updated_at TEXT,
    content_hash TEXT DEFAULT '',
    superseded_by TEXT DEFAULT ''
);

CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    title, aliases, tags, content,
    tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS embeddings (
    node_id TEXT PRIMARY KEY,
    vector BLOB,
    model TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS pending_embeddings (
    node_id TEXT PRIMARY KEY,
    queued_at TEXT
);

INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '1');
"""

_EMBED_SCAN_LIMIT = 10000


def _migrate(conn):
    """Add columns that may be missing from older databases."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(nodes)").fetchall()}
    if "content_hash" not in cols:
        conn.execute("ALTER TABLE nodes ADD COLUMN content_hash TEXT DEFAULT ''")
    if "superseded_by" not in cols:
        conn.execute("ALTER TABLE nodes ADD COLUMN superseded_by TEXT DEFAULT ''")
    conn.commit()


def _connect():
    conn = sqlite3.connect(str(INDEX_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _index_node(node_id, title, aliases, tags, content, now, content_hash=None, confidence=0.5):
    conn = _connect()
    alias_str = ", ".join(aliases) if isinstance(aliases, list) else aliases
    tag_str = ", ".join(tags) if isinstance(tags, list) else tags

    conn.execute(
        "INSERT OR REPLACE INTO nodes (id, title, aliases, status, confidence, "
        "verification_count, last_updated, tags, created_at, updated_at, content_hash) "
        "VALUES (?, ?, ?, 'active', ?, 0, ?, ?, ?, ?, ?)",
        (node_id, title, alias_str, confidence, now, tag_str, now, now, content_hash)
    )

    conn.execute("DELETE FROM nodes_fts WHERE rowid = (SELECT rowid FROM nodes WHERE id = ?)", (node_id,))
    conn.execute(
        "INSERT INTO nodes_fts (rowid, title, aliases, tags, content) "
        "VALUES ((SELECT rowid FROM nodes WHERE id = ?), ?, ?, ?, ?)",
        (node_id, title, " ".join(aliases) if isinstance(aliases, list) else aliases,
         " ".join(tags) if isinstance(tags, list) else tags, content)
    )

    # Queue embedding for async processing instead of blocking
    conn.execute(
        "INSERT OR REPLACE INTO pending_embeddings (node_id, queued_at) VALUES (?, ?)",
        (node_id, now)
    )

    conn.commit()
    conn.close()


def process_pending_embeddings():
    """Process queued embeddings. Returns count of embeddings generated."""
    init()
    conn = _connect()
    pending = conn.execute(
        "SELECT node_id, queued_at FROM pending_embeddings"
    ).fetchall()

    if not pending:
        conn.close()
        return 0

    processed = 0
    for node_id, _queued_at in pending:
        row = conn.execute(
            "SELECT title FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if not row:
            # Node was deleted; remove from queue
            conn.execute("DELETE FROM pending_embeddings WHERE node_id = ?", (node_id,))
            continue

        title = row[0]
        # Read content from FTS table
        fts_row = conn.execute(
            "SELECT content FROM nodes_fts WHERE rowid = (SELECT rowid FROM nodes WHERE id = ?)",
            (node_id,)
        ).fetchone()
        content = fts_row[0] if fts_row else ""

        vec = embed_text(f"{title} {content[:500]}")
        if vec:
            now = time.strftime("%Y-%m-%d")
            conn.execute(
                "INSERT OR REPLACE INTO embeddings (node_id, vector, model, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (node_id, _pack_vector(vec), _embed_model(), now)
            )
            conn.execute("DELETE FROM pending_embeddings WHERE node_id = ?", (node_id,))
            processed += 1
        else:
            # Embedding service unavailable; leave in queue for retry
            break

    conn.commit()
    conn.close()
    return processed


_STOPWORDS = {"a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
              "have", "has", "had", "do", "does", "did", "will", "would", "could",
              "should", "may", "might", "shall", "can", "need", "dare", "to", "of",
              "in", "for", "on", "with", "at", "by", "from", "as", "into", "about",
              "like", "through", "after", "over", "between", "out", "against", "during",
              "without", "before", "under", "around", "among", "it", "its", "this",
              "that", "these", "those", "i", "me", "my", "we", "our", "you", "your",
              "he", "him", "his", "she", "her", "they", "them", "their", "what", "which",
              "who", "when", "where", "why", "how", "not", "no", "nor", "and", "but",
              "or", "so", "if", "then", "than", "too", "very", "just"}


def _search_bm25(conn, query, limit):
    expanded = query.replace("-", " ").replace("_", " ")
    queries_to_try = [query, expanded]

    # OR fallback with stopwords removed
    meaningful = [w for w in expanded.split() if w.lower() not in _STOPWORDS and len(w) > 1]
    if meaningful:
        queries_to_try.append(" OR ".join(meaningful))

    for q in queries_to_try:
        try:
            rows = conn.execute(
                "SELECT nodes.id, nodes.title, bm25(nodes_fts) as score "
                "FROM nodes_fts JOIN nodes ON nodes_fts.rowid = nodes.rowid "
                "WHERE nodes_fts MATCH ? AND nodes.status = 'active' ORDER BY score LIMIT ?",
                (q, limit)
            ).fetchall()
            if rows:
                return [(r[0], r[1], -r[2]) for r in rows]
        except sqlite3.OperationalError:
            continue
    return []


def _search_metadata(conn, query, limit):
    terms = [t for t in query.lower().split() if len(t) > 2]
    if not terms:
        return []
    rows = conn.execute(
        "SELECT id, title, tags, aliases FROM nodes WHERE status = 'active'"
    ).fetchall()
    scored = []
    for r in rows:
        text_words = set(re.findall(r"\b\w+\b", f"{r[1]} {r[2]} {r[3]}".lower()))
        hits = sum(1 for t in terms if t in text_words)
        ratio = hits / len(terms)
        if ratio >= 0.4:
            scored.append((r[0], r[1], ratio))
    scored.sort(key=lambda x: -x[2])
    return scored[:limit]


def _search_embeddings(conn, query, limit):
    query_vec = embed_text(query)
    if not query_vec:
        return []
    count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    if count > _EMBED_SCAN_LIMIT:
        return []
    rows = conn.execute("SELECT node_id, vector FROM embeddings").fetchall()
    scored = []
    for node_id, vec_blob in rows:
        node_vec = _unpack_vector(vec_blob)
        sim = cosine_similarity(query_vec, node_vec)
        if sim > 0.3:
            title_row = conn.execute("SELECT title FROM nodes WHERE id = ?", (node_id,)).fetchone()
            title = title_row[0] if title_row else ""
            scored.append((node_id, title, sim))
    scored.sort(key=lambda x: -x[2])
    return scored[:limit]


def _rrf_fuse(result_lists, limit):
    scores = {}
    titles = {}
    for results in result_lists:
        for rank, (node_id, title, _score) in enumerate(results):
            scores[node_id] = scores.get(node_id, 0) + 1.0 / (RRF_K + rank + 1)
            titles[node_id] = title
    ranked = sorted(scores.items(), key=lambda x: -x[1])[:limit]
    return [{"id": nid, "title": titles[nid], "score": round(score, 4)} for nid, score in ranked]


def _update_mocs(node_id, title, tags):
    for tag in tags:
        moc_path = MOCS_DIR / f"{tag}.md"
        entry = f"* [[{node_id}]] — {title}\n"
        if moc_path.exists():
            content = moc_path.read_text(encoding="utf-8")
            if node_id not in content:
                content = content.rstrip() + "\n" + entry
                moc_path.write_text(content, encoding="utf-8")
        else:
            moc_path.write_text(f"# {tag}\n\n## Active\n{entry}", encoding="utf-8")


def _parse_frontmatter(text):
    match = re.match(r"^---\n(.+?)\n---", text, re.DOTALL)
    if not match:
        return {}
    meta = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            if val.startswith("[") and val.endswith("]"):
                val = [v.strip() for v in val[1:-1].split(",") if v.strip()]
            elif val.replace(".", "").isdigit():
                val = float(val) if "." in val else int(val)
            meta[key] = val
    return meta


def _extract_body(text):
    match = re.match(r"^---\n.+?\n---\n?", text, re.DOTALL)
    if match:
        return text[match.end():]
    return text


def _pack_vector(vec):
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack_vector(blob):
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))
