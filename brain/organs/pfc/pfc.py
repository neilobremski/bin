#!/usr/bin/env python3
"""PFC (prefrontal cortex) — the thinking organ.

Processes stimulus signals from other organs and generates responses.
Uses claude -p --continue to maintain conversation across cycles,
giving the brain working memory and continuity. The system prompt
(CLAUDE.md) is auto-discovered by claude in the WORKDIR.

Stimulus contract:
  IN:  "new-email <thread_id> circ:<hash>"                     (from comms)
  IN:  "sent <thread_id>"                                       (from comms)
  OUT: "send-reply <thread_id> circ:<reply_hash>"               (to comms)
  OUT: "check-email"                                             (to comms)

Each cycle (every 15 min):
1. Consume own stimulus
2. Process each signal (generate replies, log confirmations)
3. If no pending email notifications, tell comms to check email
4. Write health
"""
import json
import os
import subprocess
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
CONF_DIR = os.environ.get("CONF_DIR", str(DIR.parent.parent))

# muscles.py lives at BIN_ROOT (top-level). Spark sets PYTHONPATH.
# Fallback for manual testing: pfc is at brain/organs/pfc/, BIN_ROOT is 3 levels up.
sys.path.insert(0, str(DIR.parent.parent.parent))
import muscles


def log(msg):
    muscles.log("pfc", msg)


def generate_reply(prompt_text):
    """Generate a reply using claude -p --continue.

    The --continue flag resumes the previous conversation, giving the brain
    continuity across cycles. CLAUDE.md in the WORKDIR provides the system
    prompt automatically.
    """
    try:
        result = subprocess.run(
            ["claude", "-p", "--continue", "--model", "opus"],
            input=prompt_text,
            capture_output=True, text=True, timeout=180,
            cwd=CONF_DIR,
        )
        if result.returncode != 0:
            log(f"claude error: {result.stderr.strip()[:200]}")
            return None
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log(f"claude exception: {e}")
        return None


def handle_new_email(thread_id, circ_ref):
    """Process a new email: get content, recall memories, generate reply, send to comms."""
    raw = muscles.circ.get(circ_ref)
    if not raw:
        log(f"new-email: could not retrieve circ:{circ_ref}")
        return False

    try:
        email_data = json.loads(raw)
    except json.JSONDecodeError:
        log(f"new-email: bad JSON from circ:{circ_ref}")
        return False

    email_body = email_data.get("body", "")
    transcript = email_data.get("transcript", "")
    email_from = email_data.get("from", "unknown")
    email_subject = email_data.get("subject", "(no subject)")

    # Voice memo handling
    transcript_failed = email_data.get("transcript_failed", False)
    if transcript:
        email_body = transcript
    elif transcript_failed:
        email_body = "(This was a voice memo but I couldn't transcribe the audio. Let them know.)"
    elif not email_body.strip():
        email_body = "(empty email body)"

    # Search memories for context
    search_terms = f"{email_from} {email_subject}"
    memory_context, _ = muscles.run(
        ["memories", "search", "--", search_terms],
        timeout=15,
    )

    # Build prompt for claude --continue
    prompt_parts = []
    if memory_context:
        prompt_parts.append(
            "Here are relevant memories from your past experiences:\n"
            f"{memory_context}\n"
        )
    prompt_parts.append(
        f"You received an email from {email_from}.\n"
        f"Subject: {email_subject}\n"
        f"Body:\n{email_body}\n\n"
        "Write a reply. Keep it concise and natural. "
        "Do not include a subject line -- just the body of the reply."
    )
    prompt = "\n".join(prompt_parts)

    # Generate reply via --continue session
    reply = generate_reply(prompt)
    if not reply:
        log(f"new-email: failed to generate reply for {thread_id}")
        return False

    # Store reply in circulatory system
    reply_ref = muscles.circ.put(reply)
    if not reply_ref:
        log(f"new-email: failed to store reply in circ")
        return False

    # Tell comms to send the reply
    muscles.stimulus.send("comms", f"send-reply brain {thread_id} circ:{reply_ref}")

    # Store incoming email as memory
    muscles.memories.store(
        f"Email from {email_from} about '{email_subject}': {email_body[:200]}",
        importance=6, env=muscles.memory_env(CONF_DIR),
    )

    log(f"new-email: generated reply for {thread_id}, sent to comms")
    return True


def handle_sent(thread_id):
    """Log confirmation that a reply was sent."""
    log(f"sent: confirmed reply to {thread_id}")
    return True


def main():
    muscles.ensure_memory_db(CONF_DIR)

    lines = muscles.stimulus.consume(str(DIR)).strip().splitlines()
    processed = 0
    errors = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue

        try:
            if line.startswith("new-email"):
                parts = line.split()
                if len(parts) < 3:
                    log(f"new-email: bad format: {line}")
                    errors += 1
                    continue

                thread_id = parts[1]
                circ_ref_token = parts[2]
                if not circ_ref_token.startswith("circ:"):
                    log(f"new-email: expected circ:ref as last token: {line}")
                    errors += 1
                    continue

                circ_ref = circ_ref_token[5:]
                ok = handle_new_email(thread_id, circ_ref)
                if ok:
                    processed += 1
                else:
                    errors += 1

            elif line.startswith("sent"):
                parts = line.split()
                thread_id = parts[1] if len(parts) > 1 else "unknown"
                handle_sent(thread_id)
                processed += 1

            else:
                log(f"unknown stimulus: {line}")
                errors += 1

        except Exception as e:
            log(f"error processing '{line}': {e}")
            errors += 1

    # If no stimulus at all, tell comms to check
    if not lines:
        muscles.stimulus.send("comms", "check-email brain")
        log("no pending emails, told comms to check")

    health = f"ok processed {processed}"
    if errors:
        health += f" errors {errors}"
    if not lines:
        health = "ok idle (told comms to check)"
    (DIR / "health.txt").write_text(health + "\n")
    log(f"processed={processed} errors={errors}")


if __name__ == "__main__":
    main()
