#!/usr/bin/env python3
"""Comms organ — stimulus-driven communication I/O.

Supports two modes:
  - Mock gmail (GMAIL_MOCK_DIR set): file-based inbox/sent via JSON files
  - Real gmail (default): GAS bridge via gmail CLI muscle

Stimulus signals:
  - "check-email <reply_to> [query]" -> check for new emails, notify reply_to
  - "send-reply <reply_to> <thread_id> circ:<hash>" -> send a reply
  - "send-email <reply_to> circ:<hash>" -> compose and send new email

Each cycle: consume stimulus, process signals, write health, exit.
"""
import json
import os
import shutil
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
CONF_DIR = os.environ.get("CONF_DIR", "")
GMAIL_MOCK_DIR = os.environ.get("GMAIL_MOCK_DIR", "")

# muscles.py lives at BIN_ROOT (peer to comms/). Spark sets PYTHONPATH.
sys.path.insert(0, str(DIR.parent))
import muscles


def log(msg):
    muscles.log("comms", msg)


# ---- Mock gmail (file-based) ----

def mock_check_email(reply_to):
    """Scan GMAIL_MOCK_DIR/inbox/ for .json files, move to .processed/, notify."""
    inbox = Path(GMAIL_MOCK_DIR) / "inbox"
    if not inbox.is_dir():
        log("mock check-email: no inbox directory")
        return 0

    processed = inbox / ".processed"
    processed.mkdir(parents=True, exist_ok=True)

    count = 0
    for f in sorted(inbox.glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log(f"mock check-email: bad file {f.name}: {e}")
            continue

        payload = json.dumps({
            "id": data.get("id", f.stem),
            "from": data.get("from", "unknown"),
            "subject": data.get("subject", "(no subject)"),
            "body": data.get("body", ""),
        })
        ref = muscles.circ.put(payload)
        if not ref:
            log(f"mock check-email: failed to store {f.name} in circ")
            continue

        # Move to processed = "mark as read"
        shutil.move(str(f), str(processed / f.name))

        muscles.stimulus.send(reply_to, f"new-email {data.get('id', f.stem)} circ:{ref}")
        count += 1

    log(f"mock check-email: notified {reply_to} of {count} emails")
    return count


def mock_send_reply(reply_to, thread_id, circ_ref):
    """Write reply JSON to GMAIL_MOCK_DIR/sent/."""
    body = muscles.circ.get(circ_ref)
    if not body:
        log(f"mock send-reply: could not retrieve circ:{circ_ref}")
        return False

    sent_dir = Path(GMAIL_MOCK_DIR) / "sent"
    sent_dir.mkdir(parents=True, exist_ok=True)

    out_file = sent_dir / f"{thread_id}_reply.json"
    out_file.write_text(json.dumps({
        "id": thread_id,
        "to": reply_to,
        "subject": f"Re: {thread_id}",
        "body": body,
    }, indent=2))

    muscles.stimulus.send(reply_to, f"sent {thread_id}")
    log(f"mock send-reply: wrote {out_file.name}")
    return True


def mock_send_email(reply_to, circ_ref):
    """Write new email JSON to GMAIL_MOCK_DIR/sent/."""
    raw = muscles.circ.get(circ_ref)
    if not raw:
        log(f"mock send-email: could not retrieve circ:{circ_ref}")
        return False

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        log(f"mock send-email: circ:{circ_ref} is not valid JSON")
        return False

    to_addr = payload.get("to", "")
    subject = payload.get("subject", "")
    body_text = payload.get("body", "")
    if not to_addr or not body_text:
        log(f"mock send-email: missing 'to' or 'body'")
        return False

    sent_dir = Path(GMAIL_MOCK_DIR) / "sent"
    sent_dir.mkdir(parents=True, exist_ok=True)

    # Use a unique-ish filename from the hash
    out_file = sent_dir / f"{circ_ref[:12]}_new.json"
    out_file.write_text(json.dumps({
        "to": to_addr,
        "subject": subject,
        "body": body_text,
    }, indent=2))

    muscles.stimulus.send(reply_to, f"email-sent {to_addr}")
    log(f"mock send-email: wrote {out_file.name}")
    return True


# ---- Real gmail (GAS bridge) ----

def gmail_search(query, count=5):
    out, ok = muscles.run(["gmail", "search", query, "--count", str(count)])
    if ok and out:
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            log(f"gmail search: bad JSON: {out[:100]}")
    return None


def gmail_get(msg_id):
    out, ok = muscles.run(["gmail", "get", msg_id])
    if ok and out:
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            log(f"gmail get: bad JSON: {out[:100]}")
    return None


def gmail_reply(thread_id, body_file):
    _, ok = muscles.run(["gmail", "reply", thread_id, "--body-file", body_file])
    return ok


def gmail_read(thread_id):
    _, ok = muscles.run(["gmail", "read", thread_id])
    return ok


def real_check_email(reply_to, query=None):
    """Check Gmail for unread emails via GAS bridge, notify requesting organ."""
    if not query:
        query = "label:Tadpole is:unread"

    data = gmail_search(query, count=5)
    if not data:
        log("check-email: no results")
        return 0

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

        full = gmail_get(email_id)
        if full:
            msgs = full.get("messages", [])
            if msgs:
                msg = msgs[-1]
                email_from = msg.get("from", email_from)
                email_subject = msg.get("subject", email_subject)
                email_body = msg.get("plain", msg.get("html", ""))
            else:
                email_body = ""
        else:
            email_body = thread.get("snippet", "")

        payload = json.dumps({
            "id": email_id,
            "from": email_from,
            "subject": email_subject,
            "body": email_body,
        })
        ref = muscles.circ.put(payload)
        if not ref:
            log(f"check-email: failed to store email {email_id} in circ")
            continue

        gmail_read(email_id)
        muscles.stimulus.send(reply_to, f"new-email {email_id} circ:{ref}")
        count += 1

    log(f"check-email: notified {reply_to} of {count} emails")
    return count


def real_send_reply(reply_to, thread_id, circ_ref):
    """Send reply via GAS bridge gmail muscle."""
    circ_dir = os.environ.get("CIRC_DIR", os.path.expanduser("~/.life/circ"))
    circ_path = os.path.join(circ_dir, circ_ref)

    body = muscles.circ.get(circ_ref)
    if not body:
        log(f"send-reply: could not retrieve circ:{circ_ref}")
        return False

    ok = gmail_reply(thread_id, circ_path)
    if not ok:
        log(f"send-reply: gmail reply failed for {thread_id}")
        return False

    muscles.memories.store(
        f"Sent reply to thread {thread_id}: {body[:200]}",
        importance=5, env=muscles.memory_env(CONF_DIR),
    )
    muscles.stimulus.send(reply_to, f"sent {thread_id}")
    log(f"send-reply: replied to {thread_id}")
    return True


def real_send_email(reply_to, circ_ref):
    """Compose and send a new email via GAS bridge gmail muscle."""
    import tempfile as _tempfile

    raw = muscles.circ.get(circ_ref)
    if not raw:
        log(f"send-email: could not retrieve circ:{circ_ref}")
        return False

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        log(f"send-email: circ:{circ_ref} is not valid JSON")
        return False

    to_addr = payload.get("to", "")
    subject = payload.get("subject", "")
    body = payload.get("body", "")
    fmt = payload.get("format", "markdown")

    if not to_addr or not body:
        log(f"send-email: payload missing 'to' or 'body'")
        return False

    tmp = _tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, dir=_tempfile.gettempdir()
    )
    try:
        tmp.write(body)
        tmp.close()
        cmd = ["gmail", "send", to_addr, "--subject", subject, "--body-file", tmp.name, "--format", fmt]
        _, ok = muscles.run(cmd)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    if not ok:
        log(f"send-email: gmail send failed to {to_addr}")
        return False

    muscles.memories.store(
        f"Sent email to {to_addr} about '{subject}': {body[:200]}",
        importance=5, env=muscles.memory_env(CONF_DIR),
    )
    muscles.stimulus.send(reply_to, f"email-sent {to_addr}")
    log(f"send-email: sent to {to_addr} re: {subject}")
    return True


# ---- Dispatch (mock vs real) ----

def handle_check_email(reply_to, query=None):
    if GMAIL_MOCK_DIR:
        return mock_check_email(reply_to)
    return real_check_email(reply_to, query)


def handle_send_reply(reply_to, thread_id, circ_ref):
    if GMAIL_MOCK_DIR:
        return mock_send_reply(reply_to, thread_id, circ_ref)
    return real_send_reply(reply_to, thread_id, circ_ref)


def handle_send_email(reply_to, circ_ref):
    if GMAIL_MOCK_DIR:
        return mock_send_email(reply_to, circ_ref)
    return real_send_email(reply_to, circ_ref)


# ---- Main ----

def main():
    lines = muscles.stimulus.consume(str(DIR)).strip().splitlines()

    # Auto-check on idle when COMMS_AUTO_CHECK=1
    if not lines and os.environ.get("COMMS_AUTO_CHECK") == "1":
        lines = ["check-email brain"]

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
                parts = line.split(None, 2)
                if len(parts) < 2:
                    log(f"check-email: missing reply-to: {line}")
                    errors += 1
                    continue
                reply_to = parts[1]
                query = parts[2] if len(parts) > 2 else None
                handle_check_email(reply_to, query)
                processed += 1

            elif line.startswith("send-email"):
                parts = line.split()
                if len(parts) < 3:
                    log(f"send-email: bad format: {line}")
                    errors += 1
                    continue
                reply_to = parts[1]
                circ_ref = parts[2]
                if not circ_ref.startswith("circ:"):
                    log(f"send-email: expected circ:<hash>: {line}")
                    errors += 1
                    continue
                ok = handle_send_email(reply_to, circ_ref[5:])
                if ok:
                    processed += 1
                else:
                    errors += 1

            elif line.startswith("send-reply"):
                parts = line.split()
                if len(parts) < 4:
                    log(f"send-reply: bad format: {line}")
                    errors += 1
                    continue
                reply_to = parts[1]
                thread_id = parts[2]
                circ_ref = parts[3]
                if not circ_ref.startswith("circ:"):
                    log(f"send-reply: expected circ:ref, got: {circ_ref}")
                    errors += 1
                    continue
                ok = handle_send_reply(reply_to, thread_id, circ_ref[5:])
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
