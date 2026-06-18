"""Edition directory and manifest management."""
import json
import os
import sys
from datetime import date


def dispatch(args):
    action = args.action
    if not action:
        print("Usage: b3t edition <create|status|manifest>", file=sys.stderr)
        return 2
    if action == "create":
        return cmd_create(args)
    elif action == "status":
        return cmd_status(args)
    elif action == "manifest":
        return cmd_manifest(args)
    return 2


def _editions_dir():
    return os.path.join(os.getcwd(), "editions")


def _manifest_path():
    return os.path.join(_editions_dir(), "manifest.json")


def _load_manifest():
    path = _manifest_path()
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"editions": []}


def _save_manifest(manifest):
    path = _manifest_path()
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")


def cmd_create(args):
    """Create new edition directory structure."""
    edition_date = args.date
    title = args.title

    # Validate date format
    try:
        date.fromisoformat(edition_date)
    except ValueError:
        print(f"ERROR: Invalid date: {edition_date} (use YYYY-MM-DD)", file=sys.stderr)
        return 1

    edition_dir = os.path.join(_editions_dir(), edition_date)
    if os.path.exists(edition_dir):
        print(f"Edition directory already exists: {edition_dir}", file=sys.stderr)
        return 1

    # Create directory structure
    os.makedirs(os.path.join(edition_dir, "submissions"), exist_ok=True)
    os.makedirs(os.path.join(edition_dir, "wip"), exist_ok=True)

    # Create draft.md
    with open(os.path.join(edition_dir, "draft.md"), "w") as f:
        f.write(f"# Bear Tracks - {title}\n\n")
        f.write(f"Edition: {edition_date}\n\n")

    # Update manifest
    manifest = _load_manifest()
    entry = {
        "date": edition_date,
        "title": title,
        "status": "draft",
        "school_year": _school_year(date.fromisoformat(edition_date)),
    }

    # Check if entry already exists
    existing = [e for e in manifest["editions"] if e.get("date") == edition_date]
    if existing:
        print(f"WARNING: Manifest entry for {edition_date} already exists.", file=sys.stderr)
    else:
        manifest["editions"].append(entry)
        manifest["editions"].sort(key=lambda e: e.get("date", ""))
        _save_manifest(manifest)

    print(f"Created: {edition_dir}", file=sys.stderr)
    print(edition_dir)
    return 0


def cmd_status(args):
    """Show edition status."""
    manifest = _load_manifest()
    editions = manifest.get("editions", [])

    if hasattr(args, "date") and args.date:
        # Show specific edition
        matches = [e for e in editions if e.get("date") == args.date]
        if not matches:
            print(f"Edition {args.date} not found in manifest.", file=sys.stderr)
            return 1
        entry = matches[0]
        print(json.dumps(entry, indent=2))
    else:
        # Show latest / all in-progress
        in_progress = [e for e in editions if e.get("status") not in ("published", "unused")]
        if in_progress:
            print("In progress:")
            for e in in_progress[-5:]:
                print(f"  {e.get('date')}  [{e.get('status')}]  {e.get('title', '')}")
        else:
            # Show last few published
            print("Recent:")
            for e in editions[-5:]:
                print(f"  {e.get('date')}  [{e.get('status')}]  {e.get('title', '')}")
    return 0


def cmd_manifest(args):
    """Show full manifest."""
    manifest = _load_manifest()
    print(json.dumps(manifest, indent=2))
    return 0


def _school_year(d):
    """Determine school year string from a date."""
    year = d.year
    if d.month >= 8:
        return f"{year}-{str(year+1)[-2:]}"
    else:
        return f"{year-1}-{str(year)[-2:]}"
