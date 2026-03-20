"""organ_lib — shared primitives for tadpole organs.

Provides logging, CLI execution, stimulus I/O, circulatory system access,
and memory helpers. Organs import this instead of duplicating utilities.
"""
import json
import os
import subprocess
import sys
from pathlib import Path


def log(organ_name, msg):
    """Print a tagged message to stderr."""
    print(f"{organ_name}: {msg}", file=sys.stderr)


def run_cli(cmd, input_data=None, timeout=30):
    """Run a CLI command and return (stdout, ok). Inherits parent env."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            input=input_data,
        )
        return result.stdout.strip(), result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"cli error ({cmd[0]}): {e}", file=sys.stderr)
        return "", False


def consume_stimulus(organ_dir):
    """Read and clear pending stimulus lines for *organ_dir*."""
    out, ok = run_cli(["stimulus", "consume", str(organ_dir)])
    if ok and out:
        return out.strip().splitlines()
    return []


def stimulus_send(target, message):
    """Send a stimulus to another organ."""
    run_cli(["stimulus", "send", target, message])


def circ_put(data):
    """Store data in circulatory system, return hash."""
    out, ok = run_cli(["circ-put", "-"], input_data=data)
    if ok and out:
        return out.strip()
    return None


def circ_get(ref):
    """Retrieve data from circulatory system."""
    out, ok = run_cli(["circ-get", ref])
    if ok:
        return out
    return None


def memory_env(conf_dir):
    """Build env dict with MEMORY_DB pointing at the tadpole's hippocampus."""
    memory_db = os.environ.get(
        "MEMORY_DB",
        os.path.join(conf_dir, "organs", "hippocampus", "memory.db"),
    )
    env = os.environ.copy()
    env["MEMORY_DB"] = memory_db
    return env


def memories_store(content, importance=5, conf_dir=None):
    """Store a memory via the memories CLI."""
    if conf_dir is None:
        raise ValueError("conf_dir is required for memories_store")
    try:
        result = subprocess.run(
            ["memories", "store", "-i", str(importance), "--", content],
            capture_output=True, text=True, timeout=15,
            env=memory_env(conf_dir),
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def memories_search(query, conf_dir=None):
    """Search the tadpole's hippocampus for relevant memories."""
    if conf_dir is None:
        raise ValueError("conf_dir is required for memories_search")
    try:
        result = subprocess.run(
            ["memories", "search", "--", query],
            capture_output=True, text=True, timeout=15,
            env=memory_env(conf_dir),
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def ensure_memory_db(conf_dir):
    """Initialize memory.db if it doesn't exist yet."""
    memory_db = os.environ.get(
        "MEMORY_DB",
        os.path.join(conf_dir, "organs", "hippocampus", "memory.db"),
    )
    if os.path.isfile(memory_db):
        return
    db_dir = os.path.dirname(memory_db)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    try:
        hippocampus_dir = os.path.join(conf_dir, "organs", "hippocampus")
        sys.path.insert(0, hippocampus_dir)
        try:
            import sqlite3
            from schema import init_db, migrate
        finally:
            sys.path.pop(0)
        db = sqlite3.connect(memory_db)
        init_db(db)
        migrate(db)
        db.commit()
        db.close()
        log("organ_lib", f"initialized memory.db at {memory_db}")
    except Exception as e:
        log("organ_lib", f"could not initialize memory.db: {e}")
