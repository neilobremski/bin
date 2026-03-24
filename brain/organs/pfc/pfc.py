#!/usr/bin/env python3
"""PFC organ — prefrontal cortex. Thinks about emails using claude -p."""
import json, os, subprocess, sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
CONF_DIR = os.environ.get("CONF_DIR", str(DIR.parent.parent))
sys.path.insert(0, str(DIR.parent.parent.parent))
import muscles

log = lambda msg: muscles.log("pfc", msg)


def think(prompt):
    """Call claude -p. Returns response or None."""
    try:
        r = subprocess.run(
            ["claude", "-p"], input=prompt,
            capture_output=True, text=True, timeout=120, cwd=CONF_DIR,
        )
        if r.returncode != 0:
            log(f"claude error: {r.stderr[:200]}")
            return None
        return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log(f"claude exception: {e}")
        return None


def handle_new_email(thread_id, circ_ref):
    """Read email from circ, recall memories, think, reply via comms."""
    raw = muscles.circ.get(circ_ref)
    if not raw:
        log(f"circ miss: {circ_ref}"); return False
    try:
        email = json.loads(raw)
    except json.JSONDecodeError:
        log(f"bad JSON: {circ_ref}"); return False

    fr, subj, body = email.get("from","?"), email.get("subject",""), email.get("body","")
    mem_text, _ = muscles.run(["memories", "search", "--", f"{fr} {subj}"], timeout=15)

    parts = []
    if mem_text:
        parts.append(f"Relevant memories:\n{mem_text}\n")
    parts.append(f"Email from {fr}\nSubject: {subj}\n\n{body}\n\n"
                 "Write a reply. No subject line — just the body.")

    reply = think("\n".join(parts))
    if not reply:
        log(f"no reply for {thread_id}"); return False

    reply_ref = muscles.circ.put(reply)
    if not reply_ref:
        log("circ put failed"); return False

    muscles.stimulus.send("comms", f"send-reply brain {thread_id} circ:{reply_ref}")
    muscles.memories.store(
        f"Email from {fr} about '{subj}': {body[:200]}",
        importance=6, env=muscles.memory_env(CONF_DIR),
    )
    log(f"replied to {thread_id}"); return True


def main():
    muscles.ensure_memory_db(CONF_DIR)
    lines = muscles.stimulus.consume(str(DIR)).strip().splitlines()
    processed = errors = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            if line.startswith("new-email"):
                p = line.split()
                if len(p) < 3 or not p[2].startswith("circ:"):
                    log(f"bad format: {line}"); errors += 1; continue
                ok = handle_new_email(p[1], p[2][5:])
                processed += ok; errors += not ok
            elif line.startswith("sent"):
                tid = line.split()[1] if len(line.split()) > 1 else "?"
                log(f"confirmed sent {tid}"); processed += 1
            else:
                log(f"unknown: {line}"); errors += 1
        except Exception as e:
            log(f"error on '{line}': {e}"); errors += 1

    if not lines or all(not l.strip() for l in lines):
        muscles.stimulus.send("comms", "check-email brain")
        log("idle, told comms to check")

    status = f"ok processed={processed} errors={errors}" if lines else "ok idle"
    (DIR / "health.txt").write_text(status + "\n")


if __name__ == "__main__":
    main()
