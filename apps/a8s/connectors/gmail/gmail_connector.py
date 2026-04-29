"""Gmail connector — outbound side.

Called per a8s wake. Reads `--to`, `--subject`, `--body` from argv (the
agent's definition `invoke` substitutes `$SENDER` into `--subject` and
`$MESSAGE` into `--body` so the email subject is the sender and the body
is whatever the sender wrote).

POSTs `gmail.send` to the GAS Bridge configured via `GAS_BRIDGE_URL` /
`GAS_BRIDGE_KEY` env vars. stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


TIMEOUT_S = 30


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


def send(to: str, subject: str, body: str) -> int:
    url = os.environ.get("GAS_BRIDGE_URL", "").strip()
    key = os.environ.get("GAS_BRIDGE_KEY", "").strip()
    if not url or not key:
        print(
            "gmail-connector: GAS_BRIDGE_URL/KEY env vars must be set",
            file=sys.stderr,
        )
        return 2

    payload = {
        "action": "gmail.send",
        "key": key,
        "to": to,
        "subject": subject,
        "body": body,
    }
    try:
        result = _bridge_post(url, payload)
    except urllib.error.HTTPError as e:
        preview = ""
        try:
            preview = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        print(f"gmail-connector: HTTP {e.code} {preview}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"gmail-connector: connection error: {e.reason}", file=sys.stderr)
        return 1
    except (TimeoutError, OSError) as e:
        print(f"gmail-connector: transport error: {e}", file=sys.stderr)
        return 1

    if isinstance(result, dict) and result.get("error"):
        print(f"gmail-connector: bridge error: {result['error']}", file=sys.stderr)
        return 1

    print(f"sent to {to}: {subject}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gmail-connector")
    p.add_argument("--to", required=True)
    p.add_argument("--subject", required=True)
    p.add_argument("--body", required=True)
    args = p.parse_args(argv)
    return send(args.to, args.subject, args.body)


if __name__ == "__main__":
    raise SystemExit(main())
