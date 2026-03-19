#!/usr/bin/env python3
"""Eye organ — bridges Google Sheets to the organism.

Reads commands from a Google Sheet (input), delivers them as stimulus.
The eye does NOT write health status — that's the ganglion's job.

Sheet layout (Sheet1):
  Row 1: headers (Command, Target, Processed, Response)
  Row 2+: commands from the outside world

The eye reads unprocessed rows, calls `stimulus send <target> <command>`,
marks rows as processed, and fills in the organ's response on the next cycle.
"""
import json, os, sys
from pathlib import Path

sys.path.insert(0, os.environ.get("CONF_DIR", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
import muscles

DIR = Path(__file__).resolve().parent
SHEET_NAME = os.environ.get("SHEETS_NAME", "Tadpole")


def log(msg):
    print(f"eye: {msg}", file=sys.stderr)


def get_organ_health(organ_type):
    """Get an organ's health text by reading its health.txt directly."""
    conf_dir = os.environ.get("CONF_DIR", str(DIR.parent))
    organs = os.environ.get("ORGANS", "")
    for p in organs.split(":"):
        p = p.strip()
        if not p:
            continue
        path = Path(p) if Path(p).is_absolute() else Path(conf_dir) / p
        if path.name == organ_type and path.is_dir():
            health_file = path / "health.txt"
            if health_file.exists():
                return health_file.read_text().strip()
    return ""


def read_commands():
    """Read unprocessed commands from the sheet."""
    data = muscles.gas("sheets.read", f"name={SHEET_NAME}", "range=Sheet1!A:D")
    if not data or "rows" not in data:
        return [], []

    commands = []
    all_rows = data["rows"]
    for i, row in enumerate(all_rows):
        if i == 0:
            continue  # skip header
        while len(row) < 4:
            row.append("")
        command, target, processed, response = row[0], row[1], row[2], row[3]
        if command and not processed:
            commands.append({
                "row": i + 1,
                "command": str(command),
                "target": str(target) if target else "stomach",
            })
    return commands, all_rows


def process_commands(commands):
    """Deliver commands as stimulus and mark processed."""
    for cmd in commands:
        target = cmd["target"]
        message = cmd["command"]
        row = cmd["row"]

        ok = muscles.stimulus.send(target, message)

        muscles.gas(
            "sheets.update",
            f"name={SHEET_NAME}",
            f"range=Sheet1!C{row}:D{row}",
            f'values=[["yes","pending..."]]'
        )
        log(f"row {row}: {message} -> {target} ({'ok' if ok else 'fail'})")


def update_responses(all_rows):
    """Fill in organ responses for processed rows that still say 'pending...'."""
    for i, row in enumerate(all_rows):
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
                muscles.gas(
                    "sheets.update",
                    f"name={SHEET_NAME}",
                    f"range=Sheet1!D{i + 1}:D{i + 1}",
                    f"values={json.dumps([[health]])}"
                )
                log(f"row {i + 1}: response -> {health}")


def main():
    # Quick check: can we reach the GAS bridge?
    test = muscles.gas("info")
    if not test:
        log("no GAS bridge — idle")
        (DIR / "health.txt").write_text("ok idle (no bridge)\n")
        return

    # Read and process new commands
    commands, all_rows = read_commands()
    processed = 0
    if commands:
        process_commands(commands)
        processed = len(commands)

    # Fill in organ responses for previously processed commands
    update_responses(all_rows)

    # Report own health
    health = f"ok processed {processed}"
    (DIR / "health.txt").write_text(health + "\n")
    log(f"processed={processed}")


if __name__ == "__main__":
    main()
