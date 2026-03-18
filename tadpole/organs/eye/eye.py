#!/usr/bin/env python3
"""Eye organ — bridges Google Sheets to the organism.

Reads commands from a Google Sheet (input), delivers them as stimulus.
Writes organ health back to the sheet (output).

Sheet layout (Sheet1):
  Row 1: headers (Command, Target, Processed, Response)
  Row 2+: commands from the outside world

The eye reads unprocessed rows, calls `stimulus send <target> <command>`,
marks rows as processed, and writes the response.
"""
import json, os, sys, subprocess
from pathlib import Path
from datetime import datetime, timezone

DIR = Path(__file__).resolve().parent
SHEET_ID = os.environ.get("SHEETS_ID", "")


def log(msg):
    print(f"eye: {msg}", file=sys.stderr)


def gas(*args):
    """Call the gas CLI and return parsed JSON."""
    try:
        result = subprocess.run(
            ["gas"] + list(args),
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def stimulus_send(target, message):
    """Call stimulus send CLI."""
    try:
        subprocess.run(
            ["stimulus", "send", target, message],
            capture_output=True, timeout=10
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def stimulus_query():
    """Call stimulus query CLI, return lines."""
    try:
        result = subprocess.run(
            ["stimulus", "query"],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def read_commands():
    """Read unprocessed commands from the sheet."""
    data = gas("sheets.read", f"spreadsheet_id={SHEET_ID}", "range=Sheet1!A:D")
    if not data or "rows" not in data:
        return []

    commands = []
    for i, row in enumerate(data["rows"]):
        if i == 0:
            continue  # skip header
        # Pad row to 4 columns
        while len(row) < 4:
            row.append("")
        command, target, processed, response = row[0], row[1], row[2], row[3]
        if command and not processed:
            commands.append({
                "row": i + 1,  # 1-indexed for sheets
                "command": str(command),
                "target": str(target) if target else "stomach",  # default target
            })
    return commands


def get_organ_health(organ_type):
    """Get an organ's health text from the ganglion registry."""
    output = stimulus_query()
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) >= 5 and parts[0] == organ_type:
            return parts[4]  # health_text column
    return ""


def process_commands(commands):
    """Deliver commands as stimulus and mark processed."""
    for cmd in commands:
        target = cmd["target"]
        message = cmd["command"]
        row = cmd["row"]

        ok = stimulus_send(target, message)

        # Mark processed, response filled in later by update_responses()
        gas(
            "sheets.update",
            f"spreadsheet_id={SHEET_ID}",
            f"range=Sheet1!C{row}:D{row}",
            f'values=[["yes","pending..."]]'
        )
        log(f"row {row}: {message} -> {target} ({'ok' if ok else 'fail'})")


def update_responses():
    """Fill in organ responses for processed rows that still say 'pending...'."""
    data = gas("sheets.read", f"spreadsheet_id={SHEET_ID}", "range=Sheet1!A:D")
    if not data or "rows" not in data:
        return

    for i, row in enumerate(data["rows"]):
        if i == 0:
            continue
        while len(row) < 4:
            row.append("")
        target = str(row[1]) if row[1] else "stomach"
        processed = str(row[2])
        response = str(row[3])

        if processed == "yes" and response == "pending...":
            health = get_organ_health(target)
            if health and not health.startswith("ok idle"):
                gas(
                    "sheets.update",
                    f"spreadsheet_id={SHEET_ID}",
                    f"range=Sheet1!D{i + 1}:D{i + 1}",
                    f"values={json.dumps([[health]])}"
                )
                log(f"row {i + 1}: response updated -> {health}")


def write_health():
    """Write current organ health to the sheet (rows 1-10 of column F-I)."""
    health_output = stimulus_query()
    if not health_output:
        return

    # Build health rows: [type, status, health_text, last_seen]
    rows = [["Organ", "Status", "Health", "Updated"]]
    for line in health_output.splitlines():
        parts = line.split("\t")
        if len(parts) >= 6:
            rows.append([parts[0], parts[3], parts[4], parts[5]])

    # Pad to consistent size (max 10 organs + header)
    while len(rows) < 11:
        rows.append(["", "", "", ""])

    gas(
        "sheets.update",
        f"spreadsheet_id={SHEET_ID}",
        f"range=Sheet1!F1:I11",
        f"values={json.dumps(rows)}"
    )


def main():
    if not SHEET_ID:
        log("no SHEETS_ID configured — sleeping")
        (DIR / "health.txt").write_text("ok idle (no sheet)\n")
        return

    # Read and process new commands
    commands = read_commands()
    processed = 0
    if commands:
        process_commands(commands)
        processed = len(commands)

    # Fill in organ responses for previously processed commands
    update_responses()

    # Write health status to sheet
    write_health()

    # Report own health
    health = f"ok processed {processed}"
    (DIR / "health.txt").write_text(health + "\n")
    log(f"processed={processed}")


if __name__ == "__main__":
    main()
