"""k7e CLI — commands table, dispatcher, and main argparse entry."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import config
from engine import (
    init,
    get,
    list_nodes,
    plant,
    rebuild_mocs,
    reindex,
    search,
    stats,
    store_asset,
    tend,
)
from consolidate import consolidate
from hygiene import run_audit


COMMANDS: list[tuple[str, str, str]] = [
    ("search",      "<query> [--limit N] [--json]",    "Hybrid search (BM25 + semantic + metadata)."),
    ("get",         "<id>",                            "Read a full knowledge entry."),
    ("store",       "<title> [--tags] [--aliases]",    "Create a new entry (content from stdin or --content)."),
    ("tend",        "<id> --section <name>",           "Append to an existing entry's section."),
    ("asset",       "<file>",                          "Store binary (content-addressed, deduped). Prints path."),
    ("consolidate", "<file|dir> [--dry-run]",          "Extract knowledge from raw experience files."),
    ("reindex",     "[--embeddings]",                  "Rebuild search index from files."),
    ("rebuild-mocs", "",                               "Rebuild all Maps of Content from entry tags."),
    ("stats",       "[--json]",                        "Show knowledge store statistics."),
    ("check",       "[--fix]",                         "Audit structural integrity."),
    ("list",        "[--status] [--tag]",              "List entries with optional filters."),
    ("status",      "",                                "Show system capabilities and recommendations."),
    ("config",      "<key> [value]",                   "Get or set configuration (llm, embeddings, etc)."),
]


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="k7e",
        description="Knowledge accumulation engine.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_format_commands(),
    )
    sub = parser.add_subparsers(dest="command")

    # search
    p = sub.add_parser("search", help="Hybrid search")
    p.add_argument("query", help="Search query")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--json", action="store_true")

    # get
    p = sub.add_parser("get", help="Read entry")
    p.add_argument("id", help="Entry ID (e.g., KG-00001)")

    # store
    p = sub.add_parser("store", help="Create new entry")
    p.add_argument("title", help="Entry title")
    p.add_argument("--tags", default="", help="Comma-separated tags")
    p.add_argument("--aliases", default="", help="Comma-separated aliases")
    p.add_argument("--content", default=None, help="Content (or pipe via stdin)")

    # tend
    p = sub.add_parser("tend", help="Append to entry")
    p.add_argument("id", help="Entry ID")
    p.add_argument("--section", default="Edge Cases", help="Section to append to")
    p.add_argument("--content", default=None, help="Content (or pipe via stdin)")

    # asset
    p = sub.add_parser("asset", help="Store binary file")
    p.add_argument("file", help="Path to file")

    # consolidate
    p = sub.add_parser("consolidate", help="Extract knowledge from files")
    p.add_argument("paths", nargs="+", help="Files or directories")
    p.add_argument("--dry-run", action="store_true")

    # reindex
    p = sub.add_parser("reindex", help="Rebuild index")
    p.add_argument("--embeddings", action="store_true")

    # rebuild-mocs
    sub.add_parser("rebuild-mocs", help="Rebuild MOCs from tags")

    # stats
    p = sub.add_parser("stats", help="Statistics")
    p.add_argument("--json", action="store_true")

    # check
    p = sub.add_parser("check", help="Audit integrity")
    p.add_argument("--fix", action="store_true")

    # list
    p = sub.add_parser("list", help="List entries")
    p.add_argument("--status", default=None)
    p.add_argument("--tag", default=None)
    p.add_argument("--json", action="store_true")

    # status
    sub.add_parser("status", help="System capabilities and recommendations")

    # config
    p = sub.add_parser("config", help="Get or set configuration")
    p.add_argument("key", help="Config key (llm, embeddings, embed_model, ollama_url)")
    p.add_argument("value", nargs="?", default=None, help="Value to set (omit to read)")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    init()

    if args.command == "search":
        results = search(args.query, limit=args.limit)
        if args.json:
            print(json.dumps(results, indent=2))
        elif not results:
            print("No results.")
        else:
            for r in results:
                print(f"  {r['id']}  {r['title']}  (score: {r['score']})")

    elif args.command == "get":
        try:
            print(get(args.id))
        except FileNotFoundError as e:
            print(str(e), file=sys.stderr)
            return 1

    elif args.command == "store":
        content = args.content or sys.stdin.read()
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
        aliases = [a.strip() for a in args.aliases.split(",") if a.strip()] if args.aliases else []
        entry_id = plant(args.title, content, tags=tags, aliases=aliases)
        print(f"Stored {entry_id}: {args.title}")

    elif args.command == "tend":
        content = args.content or sys.stdin.read()
        tend(args.id, args.section, content)
        print(f"Tended {args.id} [{args.section}]")

    elif args.command == "asset":
        try:
            rel_path = store_asset(args.file)
            print(rel_path)
        except FileNotFoundError as e:
            print(str(e), file=sys.stderr)
            return 1

    elif args.command == "consolidate":
        results = consolidate(args.paths, dry_run=args.dry_run)
        for r in results:
            action = r["action"]
            title = r["title"]
            entry_id = r.get("id", "")
            print(f"  [{action}] {entry_id} {title}")
        if not results:
            print("No new knowledge extracted.")

    elif args.command == "reindex":
        reindex(embeddings=args.embeddings)
        print("Reindex complete.")

    elif args.command == "rebuild-mocs":
        rebuild_mocs()
        print("MOCs rebuilt.")

    elif args.command == "stats":
        s = stats()
        if args.json:
            print(json.dumps(s, indent=2))
        else:
            print(f"Entries: {s['total_nodes']}  MOCs: {s['total_mocs']}  Assets: {s['total_assets']}")
            print(f"Avg confidence: {s['avg_confidence']}")
            if s["top_tags"]:
                print("Top tags: " + ", ".join(f"{t}({c})" for t, c in s["top_tags"]))

    elif args.command == "check":
        issues = run_audit(fix=args.fix)
        if issues:
            for i in issues:
                print(f"  {i}")
            print(f"\n{len(issues)} issue(s).")
        else:
            print("Clean.")

    elif args.command == "list":
        nodes = list_nodes(status=args.status, tag=args.tag)
        if args.json:
            print(json.dumps(nodes, indent=2))
        else:
            for n in nodes:
                print(f"  {n['id']}  {n['title']}  [{n['status']}]  conf:{n['confidence']}")

    elif args.command == "status":
        print(config.status())

    elif args.command == "config":
        if args.value is None:
            val = config.get(args.key)
            if val is not None:
                print(val)
            else:
                print(f"{args.key}: not set")
        else:
            cfg = config.load_config()
            cfg[args.key] = args.value
            config.save_config(cfg)
            print(f"{args.key} = {args.value}")

    return 0


def _format_commands():
    lines = ["\nCommands:"]
    for name, usage, desc in COMMANDS:
        lines.append(f"  {name:14} {usage:30} {desc}")
    return "\n".join(lines)
