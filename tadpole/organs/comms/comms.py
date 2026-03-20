#!/usr/bin/env python3
"""Comms organ — stimulus-driven email I/O for the tadpole.

The comms organ does NOT check email on its own. It processes stimulus signals:
  - "check-email [query]" -> search Gmail, notify brain of new emails
  - "send-reply <thread_id> circ:<hash>" -> send a reply, notify brain

Each cycle:
1. Consume stimulus
2. Process each signal
3. Write health and exit
"""
import json
import os
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
CONF_DIR = os.environ.get("CONF_DIR", str(DIR.parent.parent))

# Make organ_lib importable
sys.path.insert(0, str(Path(CONF_DIR)))
import organ_lib


def log(msg):
    organ_lib.log("comms", msg)


def gmail_search(query, count=5):
    """Search Gmail via the gmail muscle. Returns parsed JSON or None."""
    out, ok = organ_lib.run_cli(["gmail", "search", query, "--count", str(count)])
    if ok and out:
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            log(f"gmail search: bad JSON: {out[:100]}")
    return None


def gmail_get(msg_id):
    """Get a full email via the gmail muscle."""
    out, ok = organ_lib.run_cli(["gmail", "get", msg_id])
    if ok and out:
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            log(f"gmail get: bad JSON: {out[:100]}")
    return None


def gmail_reply(thread_id, body_file):
    """Reply to a thread via the gmail muscle using --body-file."""
    _, ok = organ_lib.run_cli(["gmail", "reply", thread_id, "--body-file", body_file])
    return ok


def gmail_label(msg_id, remove=None, add=None):
    """Modify labels on a message."""
    cmd = ["gmail", "label", msg_id]
    if remove:
        cmd.extend(["--remove", remove])
    if add:
        cmd.extend(["--add", add])
    _, ok = organ_lib.run_cli(cmd)
    return ok


def handle_check_email(query=None):
    """Check Gmail for unread Tadpole emails, notify brain of each."""
    if not query:
        query = "label:Tadpole is:unread"

    data = gmail_search(query, count=5)
    if not data:
        log("check-email: no results")
        return 0

    # Handle both array and {threads/messages: [...]} shapes
    threads = data if isinstance(data, list) else data.get("threads", data.get("messages", []))
    if not threads:
        log("check-email: no unread emails")
        return 0

    count = 0
    for thread in threads:
        email_id = thread.get("id", "")
        if not email_id:
            continue

        email_from = thread.get("from", "unknown")
        email_subject = thread.get("subject", "(no subject)")

        # Get full email content
        full = gmail_get(email_id)
        if full:
            email_from = full.get("from", email_from)
            email_subject = full.get("subject", email_subject)
            email_body = full.get("body", full.get("snippet", ""))
        else:
            email_body = thread.get("snippet", "")

        # Build email payload and store in circulatory system
        payload = json.dumps({
            "id": email_id,
            "from": email_from,
            "subject": email_subject,
            "body": email_body,
        })
        ref = organ_lib.circ_put(payload)
        if not ref:
            log(f"check-email: failed to store email {email_id} in circ")
            continue

        # Notify brain — simplified format: new-email <thread_id> circ:<hash>
        organ_lib.stimulus_send("brain", f"new-email {email_id} circ:{ref}")
        count += 1

    log(f"check-email: notified brain of {count} emails")
    return count


def handle_send_reply(thread_id, circ_ref):
    """Send reply from circ, mark as read, store memory. Uses circ cache path directly."""
    # Get the circ cache path — avoids CLI arg size limits, supports attachments later
    circ_dir = os.environ.get("CIRC_DIR", os.path.expanduser("~/.life/circ"))
    circ_path = os.path.join(circ_dir, circ_ref)

    # Ensure the file exists locally (circ_get caches from Drive if needed)
    body = organ_lib.circ_get(circ_ref)
    if not body:
        log(f"send-reply: could not retrieve circ:{circ_ref}")
        return False

    ok = gmail_reply(thread_id, circ_path)
    if not ok:
        log(f"send-reply: gmail reply failed for {thread_id}")
        return False

    # Mark as read
    gmail_label(thread_id, remove="UNREAD")

    # Store interaction as memory
    organ_lib.memories_store(
        f"Sent reply to thread {thread_id}: {body[:200]}",
        importance=5, conf_dir=CONF_DIR,
    )

    # Notify brain
    organ_lib.stimulus_send("brain", f"sent {thread_id}")

    log(f"send-reply: replied to {thread_id}")
    return True


def main():
    lines = organ_lib.consume_stimulus(DIR)

    if not lines:
        (DIR / "health.txt").write_text("ok idle\n")
        return

    processed = 0
    errors = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue

        try:
            if line.startswith("check-email"):
                # "check-email" or "check-email <query>"
                parts = line.split(None, 1)
                query = parts[1] if len(parts) > 1 else None
                n = handle_check_email(query)
                processed += 1

            elif line.startswith("send-reply"):
                # "send-reply <thread_id> circ:<hash>"
                parts = line.split()
                if len(parts) < 3:
                    log(f"send-reply: bad format: {line}")
                    errors += 1
                    continue
                thread_id = parts[1]
                circ_ref = parts[2]
                if not circ_ref.startswith("circ:"):
                    log(f"send-reply: expected circ:ref, got: {circ_ref}")
                    errors += 1
                    continue
                ref = circ_ref[5:]  # strip "circ:"
                ok = handle_send_reply(thread_id, ref)
                if ok:
                    processed += 1
                else:
                    errors += 1

            else:
                log(f"unknown stimulus: {line}")
                errors += 1

        except Exception as e:
            log(f"error processing '{line}': {e}")
            errors += 1

    health = f"ok processed {processed}"
    if errors:
        health += f" errors {errors}"
    (DIR / "health.txt").write_text(health + "\n")
    log(f"processed={processed} errors={errors}")


if __name__ == "__main__":
    main()
