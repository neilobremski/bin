#!/usr/bin/env python3
"""Ganglion — nervous system local node.

Scans local organs, journals health changes, broadcasts via MQTT, delivers stimulus.
One per body part. SQLite registry tracks all known organs.
"""
import os, sys, sqlite3, json, subprocess
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, os.environ.get("CONF_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import muscles

# --- Configuration from environment (set by spark via life.conf) ---
DB_PATH = os.environ.get("GANGLION_DB", os.path.expanduser("~/.life/ganglion.db"))
BODY = os.environ.get("BODY_PART", "local")
ORGANS = os.environ.get("ORGANS", "")
MQTT_HOST = os.environ.get("MQTT_HOST", "")
CLIENT_ID = os.environ.get("GANGLION_CLIENT_ID", f"{BODY}-ganglion")
DIR = Path(__file__).resolve().parent
CONF_DIR = Path(os.environ["CONF_DIR"]) if "CONF_DIR" in os.environ else DIR.parent


def log(msg):
    print(f"ganglion: {msg}", file=sys.stderr)


def init_db(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS organs (
            type TEXT NOT NULL,
            id TEXT NOT NULL,
            body_part TEXT NOT NULL,
            health_status TEXT DEFAULT '',
            health_text TEXT DEFAULT '',
            last_seen TEXT DEFAULT '',
            PRIMARY KEY (type, id)
        );
        CREATE TABLE IF NOT EXISTS health_log (
            type TEXT NOT NULL,
            id TEXT NOT NULL,
            ts TEXT NOT NULL,
            status TEXT NOT NULL,
            health_text TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_health_log_type ON health_log(type, id, ts);
    """)


def resolve_organs():
    """Parse ORGANS env var into list of (path, type) tuples."""
    if not ORGANS:
        return []
    result = []
    for p in ORGANS.split(":"):
        p = p.strip()
        if not p:
            continue
        path = Path(p) if Path(p).is_absolute() else CONF_DIR / p
        result.append((path, path.name))
    return result


def scan_local(db, organ_list):
    """Phase 1+2: Scan local organs, update registry, journal health changes."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    scanned = 0

    # Count types for ID assignment
    type_counts = {}
    for path, organ_type in organ_list:
        if organ_type == "ganglion":
            continue
        type_counts[organ_type] = type_counts.get(organ_type, 0) + 1

    type_idx = {}
    for path, organ_type in organ_list:
        if organ_type == "ganglion" or not path.is_dir():
            continue

        # Build organ ID
        type_idx[organ_type] = type_idx.get(organ_type, 0) + 1
        if type_counts.get(organ_type, 0) > 1:
            organ_id = f"{organ_type}-{BODY}-{type_idx[organ_type]}"
        else:
            organ_id = f"{organ_type}-{BODY}"

        # Read health
        health_file = path / "health.txt"
        health_text = ""
        health_status = ""
        if health_file.exists():
            try:
                health_text = health_file.read_text().strip()
                health_status = health_text.split()[0] if health_text else ""
            except OSError:
                pass

        # Upsert into registry
        db.execute("""
            INSERT INTO organs(type, id, body_part, health_status, health_text, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(type, id) DO UPDATE SET
                body_part=excluded.body_part,
                health_status=excluded.health_status,
                health_text=excluded.health_text,
                last_seen=excluded.last_seen
        """, (organ_type, organ_id, BODY, health_status, health_text, now))
        scanned += 1

        # Journal health changes (duplicate collapsing)
        row = db.execute(
            "SELECT status FROM health_log WHERE type=? AND id=? ORDER BY ts DESC LIMIT 1",
            (organ_type, organ_id)
        ).fetchone()
        last_status = row[0] if row else None

        if last_status != health_status:
            db.execute(
                "INSERT INTO health_log(type, id, ts, status, health_text) VALUES (?, ?, ?, ?, ?)",
                (organ_type, organ_id, now, health_status, health_text)
            )

    db.commit()
    return scanned


def mqtt_broadcast(db):
    """Phase 3a: Broadcast local registry to other ganglions."""
    if not MQTT_HOST:
        return
    rows = db.execute(
        "SELECT type, id, body_part, health_status, health_text, last_seen FROM organs WHERE body_part=?",
        (BODY,)
    ).fetchall()
    payload = json.dumps([
        {"type": r[0], "id": r[1], "body_part": r[2],
         "health_status": r[3], "health_text": r[4], "last_seen": r[5]}
        for r in rows
    ])
    muscles.mqtt.pub(topic=f"life/{BODY}/registry", message=payload, retain=True, timeout=5)


def mqtt_receive(db):
    """Phase 3b: Receive other ganglions' registries and merge."""
    if not MQTT_HOST:
        return
    try:
        result = subprocess.run(
            ["mqtt-sub", "-t", "life/+/registry", "-W", "1", "-C", "5", "-v"],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return

    if not output:
        return

    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split(" ", 1)
        if len(parts) < 2:
            continue
        topic, payload = parts
        # Extract body part from topic: life/<body_part>/registry
        topic_parts = topic.split("/")
        if len(topic_parts) < 3:
            continue
        remote_body = topic_parts[1]
        if remote_body == BODY:
            continue

        try:
            data = json.loads(payload)
            for row in data:
                db.execute("""
                    INSERT INTO organs(type, id, body_part, health_status, health_text, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(type, id) DO UPDATE SET
                        body_part=excluded.body_part,
                        health_status=excluded.health_status,
                        health_text=excluded.health_text,
                        last_seen=excluded.last_seen
                """, (
                    row.get("type", ""), row.get("id", ""), row.get("body_part", ""),
                    row.get("health_status", ""), row.get("health_text", ""),
                    row.get("last_seen", "")
                ))
            db.commit()
        except (json.JSONDecodeError, KeyError):
            pass


def mqtt_drain_stimulus(db, organ_list):
    """Phase 3c: Drain stimulus messages from MQTT and deliver to local organs."""
    if not MQTT_HOST:
        return 0
    try:
        result = subprocess.run(
            ["mqtt-sub", "-t", "life/+/stimulus/#", "-W", "2", "-C", "10",
             "-v", "-i", CLIENT_ID, "-c"],
            capture_output=True, text=True, timeout=15
        )
        output = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 0

    if not output:
        return 0

    # Build local organ lookup: type -> path
    local_organs = {}
    for path, organ_type in organ_list:
        if organ_type != "ganglion" and path.is_dir():
            local_organs[organ_type] = path

    routed = 0
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split(" ", 1)
        if len(parts) < 2:
            continue
        topic, message = parts
        target_type = topic.rstrip("/").split("/")[-1]

        if target_type == "ganglion":
            continue

        if target_type in local_organs:
            stim_file = local_organs[target_type] / "stimulus.txt"
            with open(stim_file, "a") as f:
                f.write(message + "\n")
            routed += 1
            log(f"life/stimulus/{target_type} -> {target_type}")

    return routed


def main():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    init_db(db)

    organ_list = resolve_organs()

    # Phase 1+2: Scan + journal
    scanned = scan_local(db, organ_list)

    # Phase 3: MQTT
    mqtt_broadcast(db)
    mqtt_receive(db)
    routed = mqtt_drain_stimulus(db, organ_list)

    # Report health
    health = f"ok scanned {scanned} routed {routed}"
    (DIR / "health.txt").write_text(health + "\n")
    log(f"scanned={scanned} routed={routed}")

    db.close()


if __name__ == "__main__":
    main()
