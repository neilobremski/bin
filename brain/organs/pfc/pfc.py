#!/usr/bin/env python3
"""PFC organ — reads email stimulus, generates replies via claude -p."""
import json, os, subprocess, sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, os.environ.get("PYTHONPATH", "").split(":")[0] or str(DIR.parent.parent.parent))
import muscles

log = lambda msg: muscles.log("pfc", msg)

def generate_reply(email_from, email_subject, email_body, memory_ctx):
    """Generate a reply using claude -p (no flags — CLAUDE.md provides personality)."""
    parts = []
    if memory_ctx:
        parts.append(f"Here are relevant memories:\n{memory_ctx}\n")
    parts.append(
        f"You received an email from {email_from}.\n"
        f"Subject: {email_subject}\nBody:\n{email_body}\n\n"
        "Write a reply. Concise and natural. No subject line."
    )
    cwd = os.environ.get("CLAUDE_CWD", "/opt/bin/tadpole")
    try:
        r = subprocess.run(["claude", "-p"], input="\n".join(parts),
                           capture_output=True, text=True, timeout=180, cwd=cwd)
        if r.returncode != 0:
            log(f"claude error: {r.stderr.strip()}")
            return None
        return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log(f"claude exception: {e}")
        return None

def handle_new_email(thread_id, circ_ref):
    raw = muscles.circ.get(circ_ref)
    if not raw:
        log(f"new-email: could not retrieve circ:{circ_ref}"); return False
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        log(f"new-email: bad JSON from circ:{circ_ref}"); return False
    body, frm, subj = d.get("body",""), d.get("from","unknown"), d.get("subject","(no subject)")
    mem_ctx, _ = muscles.run(["memories","search","--",f"{frm} {subj}"], timeout=15)
    reply = generate_reply(frm, subj, body, mem_ctx)
    if not reply:
        log(f"new-email: no reply for {thread_id}"); return False
    ref = muscles.circ.put(reply)
    if not ref:
        log("new-email: circ.put failed"); return False
    muscles.stimulus.send("comms", f"send-reply pfc {thread_id} circ:{ref}")
    log(f"new-email: reply for {thread_id} sent to comms")
    return True

def main():
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
                processed += ok; errors += (not ok)
            else:
                log(f"unknown stimulus: {line}"); errors += 1
        except Exception as e:
            log(f"error: {e}"); errors += 1
    if not lines:
        muscles.stimulus.send("comms", "check-email pfc")
        log("idle, told comms to check")
    health = "ok idle (told comms to check)" if not lines else f"ok processed {processed}"
    if errors:
        health += f" errors {errors}"
    (DIR / "health.txt").write_text(health + "\n")

if __name__ == "__main__":
    main()
