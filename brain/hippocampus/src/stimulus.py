"""Stimulus processing: read .stimulus/*.json, dispatch actions, respond.

Protocol:
    Every stimulus JSON must include:
        "action": one of "store", "search", "recall", "stats"
        "id": correlation ID (caller generates, response echoes it)
        "from": organ name to send response to

    Store:  {"action":"store", "content":"...", "importance":5, "category":"general",
             "id":"abc", "from":"brain"}
    Search: {"action":"search", "query":"...", "limit":10, "id":"abc", "from":"brain"}
    Recall: {"action":"recall", "limit":5, "id":"abc", "from":"brain"}
    Stats:  {"action":"stats", "id":"abc", "from":"brain"}

    Responses go via: stimulus send --to <from> --body '<json>'
    Large results go via circ push, with the hash in the response body.
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

from constants import DIR, log
from storage import store_and_check
from retrieval import search, recent, by_importance, stats


STIMULUS_DIR = DIR / ".stimulus"


def consume_stimulus_files():
    """Read all .stimulus/*.json files in sorted order.

    Returns list of parsed dicts. Deletes each file after successful parse.
    Bad JSON files are logged and deleted.
    """
    if not STIMULUS_DIR.exists():
        return []

    files = sorted(STIMULUS_DIR.glob("*.json"))
    stimuli = []
    for f in files:
        try:
            data = json.loads(f.read_text())
            stimuli.append(data)
        except (json.JSONDecodeError, OSError) as e:
            log(f"bad stimulus {f.name}: {e}")
        f.unlink(missing_ok=True)
    return stimuli


def _send_response(target, response_data):
    """Send a JSON response via stimulus send --to <target>."""
    body = json.dumps(response_data, default=str)
    try:
        subprocess.run(
            ["stimulus", "send", "--to", target, "--body", body],
            capture_output=True, timeout=10
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log(f"failed to send response to {target}: {e}")


def _circ_push(data_str):
    """Push data via circ, return hash or None."""
    try:
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        tmp.write(data_str)
        tmp.close()
        result = subprocess.run(
            ["circ", "push", tmp.name],
            capture_output=True, text=True, timeout=10
        )
        os.unlink(tmp.name)
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _handle_store(db, stim):
    """Handle a store action."""
    content = stim.get("content", "")
    importance = stim.get("importance", 5)
    category = stim.get("category", "general")
    source = stim.get("from", "stimulus")

    mid = store_and_check(db, content, importance, category, source=source)

    return {
        "id": stim.get("id"),
        "action": "store",
        "status": "stored" if mid else "rejected",
        "memory_id": mid,
    }


def _handle_search(db, stim):
    """Handle a search action. Returns results via circ for large payloads."""
    query = stim.get("query", "")
    limit = min(stim.get("limit", 10), 50)

    results = search(db, query, limit=limit)
    db.commit()

    result_list = []
    for row in results:
        result_list.append({
            "id": row[0],
            "content": row[1],
            "importance": row[2],
            "category": row[3],
            "created_at": row[5],
        })

    response = {
        "id": stim.get("id"),
        "action": "search",
        "query": query,
        "count": len(result_list),
    }

    # Push results via circ if available, inline if not
    payload = json.dumps(result_list, default=str)
    circ_hash = _circ_push(payload)
    if circ_hash:
        response["results_hash"] = circ_hash
    else:
        response["results"] = result_list

    return response


def _handle_recall(db, stim):
    """Handle a recall action: recent + high-importance memories."""
    limit = min(stim.get("limit", 5), 20)

    recent_rows = recent(db, limit=limit)
    important_rows = by_importance(db, limit=limit)

    # Merge and deduplicate
    seen = set()
    result_list = []
    for row in list(important_rows) + list(recent_rows):
        if row[0] not in seen:
            seen.add(row[0])
            result_list.append({
                "id": row[0],
                "content": row[1],
                "importance": row[2],
                "category": row[3],
                "created_at": row[5],
            })

    response = {
        "id": stim.get("id"),
        "action": "recall",
        "count": len(result_list),
    }

    payload = json.dumps(result_list, default=str)
    circ_hash = _circ_push(payload)
    if circ_hash:
        response["results_hash"] = circ_hash
    else:
        response["results"] = result_list

    return response


def _handle_stats(db, stim):
    """Handle a stats action."""
    s = stats(db)
    return {
        "id": stim.get("id"),
        "action": "stats",
        **s,
    }


HANDLERS = {
    "store": _handle_store,
    "search": _handle_search,
    "recall": _handle_recall,
    "stats": _handle_stats,
}


def process_stimuli(db, stimuli):
    """Process a list of stimulus dicts, dispatch to handlers, send responses.

    Returns count of successfully processed stimuli.
    """
    processed = 0
    for stim in stimuli:
        action = stim.get("action")
        sender = stim.get("from")
        corr_id = stim.get("id")

        if not action:
            log(f"stimulus missing action, skipping")
            continue

        handler = HANDLERS.get(action)
        if not handler:
            log(f"unknown action: {action}")
            if sender and corr_id:
                _send_response(sender, {
                    "id": corr_id,
                    "action": action,
                    "error": f"unknown action: {action}",
                })
            continue

        try:
            response = handler(db, stim)
            if sender:
                _send_response(sender, response)
            processed += 1
        except Exception as e:
            log(f"error handling {action}: {e}")
            if sender and corr_id:
                _send_response(sender, {
                    "id": corr_id,
                    "action": action,
                    "error": str(e),
                })

    return processed
