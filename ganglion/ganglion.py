#!/usr/bin/env python3
"""Ganglion — nervous system local node.

Scans local organs, journals health changes, broadcasts via MQTT, delivers stimulus.
One per body part. SQLite registry tracks all known organs.

Persistent listener mode: after quick phases (scan, journal, broadcast), listens on
MQTT for ~50 seconds. When stimulus arrives, immediately delivers to the target organ
and sparks it. Also periodically checks for local stimulus during the listen window.
"""
import os, sys, sqlite3, json, subprocess, time
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

# Path to spark-organ.sh (sibling directory to ganglion)
SPARK_ORGAN_SCRIPT = DIR.parent / "life" / "spark-organ.sh"


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


def build_organ_lookup(organ_list):
    """Build {name: Path} map from organ list, excluding ganglion itself."""
    lookup = {}
    for path, organ_type in organ_list:
        if organ_type != "ganglion" and path.is_dir():
            lookup[organ_type] = path
    return lookup


def spark_organ(organ_path):
    """Spark a single organ with flock locking. Non-blocking — skips if already running."""
    name = organ_path.name if isinstance(organ_path, Path) else os.path.basename(organ_path)
    organ_path_str = str(organ_path)

    if SPARK_ORGAN_SCRIPT.is_file():
        # Use spark-organ.sh which handles locking, env setup, and execution
        subprocess.Popen(
            [str(SPARK_ORGAN_SCRIPT), organ_path_str],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    else:
        # Fallback: direct launch with flock
        lock_dir = os.path.expanduser("~/.life/locks")
        os.makedirs(lock_dir, exist_ok=True)
        lock_file = os.path.join(lock_dir, f"{name}.lock")
        subprocess.Popen(
            f'exec 9>"{lock_file}"; flock -n 9 || exit 0; cd "{organ_path_str}" && bash live.sh >> .spark.log 2>&1',
            shell=True
        )

    log(f"sparked {name}")


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


def check_local_stimulus(organ_lookup):
    """Check all organs for pending local stimulus and spark any that have content."""
    sparked = 0
    for organ_type, organ_path in organ_lookup.items():
        stim_file = organ_path / "stimulus.txt"
        if stim_file.exists() and stim_file.stat().st_size > 0:
            spark_organ(organ_path)
            sparked += 1
    return sparked


def mqtt_listen_and_spark(organ_lookup, duration=50):
    """Listen on MQTT for stimulus, deliver and spark target organs immediately.

    Runs mqtt-sub for `duration` seconds. Each incoming line triggers immediate
    stimulus delivery + organ spark. Simple readline loop — mqtt-sub outputs
    one line per message and exits after -W timeout or -C message count.
    """
    if not MQTT_HOST or duration <= 0:
        return 0

    routed = 0
    proc = None

    try:
        proc = subprocess.Popen(
            ["mqtt-sub", "-t", "life/+/stimulus/#", "-W", str(duration), "-C", "100",
             "-v", "-i", CLIENT_ID, "-c"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        )

        # Simple blocking readline — mqtt-sub flushes each line
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue

            parts = line.split(" ", 1)
            if len(parts) < 2:
                continue
            topic, payload = parts

            # Extract target organ type from topic: life/<body>/stimulus/<type>
            segments = topic.split("/")
            if len(segments) < 4:
                continue
            target_type = segments[-1]

            if target_type == "ganglion":
                continue

            organ_path = organ_lookup.get(target_type)
            if not organ_path:
                log(f"stimulus for unknown organ '{target_type}' — ignoring")
                continue

            # Write stimulus and spark immediately
            stim_file = organ_path / "stimulus.txt"
            with open(stim_file, "a") as f:
                f.write(payload + "\n")
            spark_organ(organ_path)
            routed += 1
            log(f"mqtt stimulus -> {target_type} (sparked)")

        proc.wait(timeout=5)
    except FileNotFoundError:
        log("mqtt-sub not found — skipping listen phase")
    except Exception as e:
        log(f"listen error: {e}")
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    return routed


def main():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    init_db(db)

    organ_list = resolve_organs()
    organ_lookup = build_organ_lookup(organ_list)

    # Phase 1+2: Scan + journal
    scanned = scan_local(db, organ_list)

    # Phase 3: MQTT broadcast + receive (quick, ~5 seconds total)
    mqtt_broadcast(db)
    mqtt_receive(db)

    # Phase 4: Check for any local stimulus before entering listen mode
    local_sparked = check_local_stimulus(organ_lookup)
    if local_sparked > 0:
        log(f"pre-listen local stimulus: sparked {local_sparked} organs")

    # Phase 5: Persistent MQTT listen (~50 seconds)
    # Delivers stimulus and sparks organs on-demand as messages arrive
    listen_duration = int(os.environ.get("GANGLION_LISTEN_DURATION", "50"))
    routed = mqtt_listen_and_spark(organ_lookup, duration=listen_duration)

    # Final local stimulus check after listen window closes
    final_sparked = check_local_stimulus(organ_lookup)
    if final_sparked > 0:
        log(f"post-listen local stimulus: sparked {final_sparked} organs")

    total_sparked = local_sparked + final_sparked

    # Report health
    health = f"ok scanned {scanned} routed {routed} sparked {total_sparked}"
    (DIR / "health.txt").write_text(health + "\n")
    log(f"scanned={scanned} routed={routed} sparked={total_sparked}")

    db.close()


if __name__ == "__main__":
    main()
