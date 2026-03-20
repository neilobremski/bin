#!/usr/bin/env python3
"""Brain organ — decision-making center for the tadpole.

Processes stimulus signals from other organs and generates responses.

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

# Make organ_lib importable
sys.path.insert(0, str(Path(CONF_DIR)))
import organ_lib

SYSTEM_PROMPT = (
    "You are Tadpole, a tiny organism learning about the world through email "
    "conversations. You have a hippocampus that stores memories. Use the context "
    "from your memories to inform your replies. Be curious, playful, and genuine. "
    "Your personality will develop over time based on your experiences."
)


def log(msg):
    organ_lib.log("brain", msg)


def generate_reply(email_from, email_subject, email_body, memory_context):
    """Generate a reply using Claude Haiku."""
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

    try:
        result = subprocess.run(
            ["claude", "-p", "--model", "haiku",
             "--append-system-prompt", SYSTEM_PROMPT],
            input=prompt,
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            log(f"claude error: {result.stderr.strip()}")
            return None
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log(f"claude exception: {e}")
        return None


def handle_new_email(thread_id, circ_ref):
    """Process a new email: get content, recall memories, generate reply, send to comms."""
    # Get email content from circ
    raw = organ_lib.circ_get(circ_ref)
    if not raw:
        log(f"new-email: could not retrieve circ:{circ_ref}")
        return False

    try:
        email_data = json.loads(raw)
    except json.JSONDecodeError:
        log(f"new-email: bad JSON from circ:{circ_ref}")
        return False

    email_body = email_data.get("body", "")
    email_from = email_data.get("from", "unknown")
    email_subject = email_data.get("subject", "(no subject)")

    # Search memories for context
    search_terms = f"{email_from} {email_subject}"
    memory_context = organ_lib.memories_search(search_terms, conf_dir=CONF_DIR)

    # Generate reply
    reply = generate_reply(email_from, email_subject, email_body, memory_context)
    if not reply:
        log(f"new-email: failed to generate reply for {thread_id}")
        return False

    # Store reply in circulatory system
    reply_ref = organ_lib.circ_put(reply)
    if not reply_ref:
        log(f"new-email: failed to store reply in circ")
        return False

    # Tell comms to send the reply
    organ_lib.stimulus_send("comms", f"send-reply {thread_id} circ:{reply_ref}")

    # Store incoming email as memory
    organ_lib.memories_store(
        f"Email from {email_from} about '{email_subject}': {email_body[:200]}",
        importance=6, conf_dir=CONF_DIR,
    )

    log(f"new-email: generated reply for {thread_id}, sent to comms")
    return True


def handle_sent(thread_id):
    """Log confirmation that a reply was sent."""
    log(f"sent: confirmed reply to {thread_id}")
    return True


def main():
    organ_lib.ensure_memory_db(CONF_DIR)

    lines = organ_lib.consume_stimulus(DIR)
    processed = 0
    errors = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue

        try:
            if line.startswith("new-email"):
                # "new-email <thread_id> circ:<hash>"
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
                # "sent <thread_id>"
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
        organ_lib.stimulus_send("comms", "check-email")
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
