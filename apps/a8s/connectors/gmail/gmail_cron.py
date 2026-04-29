"""Gmail connector — inbound side, run from cron.

Polls the GAS Bridge inbox for unread mail FROM the configured `--to`
address (the address this connector sends to — replies come back from
there). Strips `Re:` / `Fwd:` repeats from each subject, resolves the
remaining bare token to an a8s participant, and shells `tell <name>
<body>` from the connector's registered root so a8s force-stamps `from`
correctly.

stdlib only. Uses `registry.resolve_name` for participant lookup so
aliases / cycle detection match the rest of a8s.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


TIMEOUT_S = 30


# ---------- importing the a8s package ----------
#
# This script lives at apps/a8s/connectors/gmail/gmail_cron.py. Walking two
# parents up gets us to apps/a8s/, which is what conftest.py and a8s.py both
# put on sys.path so `import registry` works.
_A8S_DIR = Path(__file__).resolve().parent.parent.parent
if str(_A8S_DIR) not in sys.path:
    sys.path.insert(0, str(_A8S_DIR))


# ---------- subject parse ----------

_PREFIXES = ("re:", "fwd:", "fw:")


def parse_subject_to_name(subject: str) -> str:
    """Strip leading `re:` / `fwd:` / `fw:` prefixes (case-insensitive,
    repeated, optional whitespace after the colon) until none remain.
    Returns the stripped subject — what's left is the participant-name
    candidate, which the caller passes to `registry.resolve_name`.

    Examples:
      'Re: NEIL'        -> 'NEIL'
      'Re: Re: NEIL'    -> 'NEIL'
      'RE:NEIL'         -> 'NEIL'
      'Fwd: NEIL'       -> 'NEIL'
      're: fwd: NEIL'   -> 'NEIL'
      'NEIL urgent'     -> 'NEIL urgent'   (no prefix, leave as-is)
      ''                -> ''              (graceful)
    """
    s = (subject or "").strip()
    while True:
        lower = s.lower()
        matched = False
        for pref in _PREFIXES:
            if lower.startswith(pref):
                s = s[len(pref):].lstrip()
                matched = True
                break
        if not matched:
            break
    return s


# ---------- bridge HTTP ----------

def _bridge_post(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"error": f"non-JSON response: {body[:200]}"}


# ---------- definition reading ----------

def _to_from_definition(def_path: Path) -> str:
    """Pull the `--to` value out of the definition's `invoke` argv. Errors
    loudly if the definition lacks one — the cron job has no other way to
    know which address replies will arrive from."""
    try:
        with def_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"gmail-cron: failed to read definition {def_path}: {e}")
    invoke = data.get("invoke") or []
    for i, arg in enumerate(invoke):
        if arg == "--to" and i + 1 < len(invoke):
            return str(invoke[i + 1]).strip()
    raise SystemExit(
        f"gmail-cron: definition {def_path} has no '--to' in invoke argv"
    )


# ---------- connector root lookup ----------

def _connector_root_for_definition(def_path: Path) -> Path | None:
    """Find the registered agent whose `definition` matches `def_path` and
    return its root. Used as cwd for the `tell` shell-out so a8s
    force-stamps `from` to the connector's participant name."""
    from registry import load_registry  # late import — sys.path is set above
    reg = load_registry()
    target = def_path.resolve()
    for name, info in reg.items():
        d = info.get("definition") or ""
        if not d:
            continue
        try:
            if Path(d).expanduser().resolve() == target:
                return Path(info.get("root", "")).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
    return None


# ---------- main flow ----------

def _last_message(thread: dict) -> dict | None:
    msgs = thread.get("messages") or []
    return msgs[-1] if msgs else None


def _body_of(msg: dict) -> str:
    return (msg.get("plain") or msg.get("body") or "").strip()


def _resolve(name_candidate: str) -> str | None:
    """Return canonical participant name or None if unknown."""
    from registry import resolve_name
    try:
        kind, names = resolve_name(name_candidate)
    except (KeyError, ValueError):
        return None
    if not names:
        return None
    # For an alias the message goes through `tell <alias>` (a8s expands).
    # We pass the original token so opacity/fan-out match the normal path.
    if kind == "alias":
        return name_candidate
    return names[0]


def _process_thread(
    thread_summary: dict,
    *,
    bridge_url: str,
    bridge_key: str,
    cwd: Path,
) -> tuple[bool, bool]:
    """Process a single thread. Returns (attempted, succeeded).

    `attempted` is True if we tried a tell (i.e. resolved a participant).
    `succeeded` is True iff the tell exited 0 AND mark-read succeeded.
    """
    thread_id = thread_summary.get("id")
    if not thread_id:
        return False, False

    try:
        full = _bridge_post(bridge_url, {
            "action": "gmail.get",
            "key": bridge_key,
            "thread_id": thread_id,
        })
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        print(f"[gmail-cron] gmail.get failed for {thread_id}: {e}", file=sys.stderr)
        return False, False
    if isinstance(full, dict) and full.get("error"):
        print(f"[gmail-cron] gmail.get error: {full['error']}", file=sys.stderr)
        return False, False

    msg = _last_message(full)
    if msg is None:
        print(f"[gmail-cron] thread {thread_id} has no messages", file=sys.stderr)
        return False, False

    subject = msg.get("subject") or ""
    body = _body_of(msg)
    candidate = parse_subject_to_name(subject)
    name = _resolve(candidate) if candidate else None
    if name is None:
        print(
            f"[gmail-cron] subject \"{subject}\" matches no participant; left unread",
            file=sys.stderr,
        )
        return False, False

    try:
        result = subprocess.run(
            ["tell", name, body],
            cwd=str(cwd),
            check=False,
        )
    except (OSError, FileNotFoundError) as e:
        print(f"[gmail-cron] tell shell-out failed: {e}", file=sys.stderr)
        return True, False
    if result.returncode != 0:
        print(
            f"[gmail-cron] tell {name} returned {result.returncode}; left unread",
            file=sys.stderr,
        )
        return True, False

    try:
        read = _bridge_post(bridge_url, {
            "action": "gmail.read",
            "key": bridge_key,
            "thread_id": thread_id,
        })
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        print(f"[gmail-cron] gmail.read failed for {thread_id}: {e}", file=sys.stderr)
        return True, False
    if isinstance(read, dict) and read.get("error"):
        print(f"[gmail-cron] gmail.read error: {read['error']}", file=sys.stderr)
        return True, False
    return True, True


def run(def_path: Path) -> int:
    bridge_url = os.environ.get("GAS_BRIDGE_URL", "").strip()
    bridge_key = os.environ.get("GAS_BRIDGE_KEY", "").strip()
    if not bridge_url or not bridge_key:
        print(
            "gmail-cron: GAS_BRIDGE_URL/KEY env vars must be set",
            file=sys.stderr,
        )
        return 2

    to_addr = _to_from_definition(def_path)
    cwd = _connector_root_for_definition(def_path)
    if cwd is None:
        print(
            f"gmail-cron: no registered agent uses {def_path} as its definition; "
            "register one with `a8s add <name> <root> <def-path>`",
            file=sys.stderr,
        )
        return 2

    try:
        search = _bridge_post(bridge_url, {
            "action": "gmail.search",
            "key": bridge_key,
            "query": f"is:unread from:{to_addr}",
            "count": 20,
        })
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        print(f"gmail-cron: gmail.search failed: {e}", file=sys.stderr)
        return 1
    if isinstance(search, dict) and search.get("error"):
        print(f"gmail-cron: bridge error: {search['error']}", file=sys.stderr)
        return 1

    threads = (search or {}).get("messages") or []
    if not threads:
        return 0

    attempts = 0
    successes = 0
    for t in threads:
        attempted, ok = _process_thread(
            t,
            bridge_url=bridge_url,
            bridge_key=bridge_key,
            cwd=cwd,
        )
        if attempted:
            attempts += 1
            if ok:
                successes += 1

    # Exit 1 only if we tried at least once and every attempt failed.
    if attempts > 0 and successes == 0:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gmail-cron")
    p.add_argument("--from-def", dest="from_def", required=True,
                   help="path to the connector's a8s definition JSON")
    args = p.parse_args(argv)
    return run(Path(args.from_def).expanduser())


if __name__ == "__main__":
    raise SystemExit(main())
