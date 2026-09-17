"""Microsoft Teams chat and channel sweeping.

Usage:
    b3t teams login                     # Verify Teams auth
    b3t teams list                      # List chats and channels
    b3t teams sweep                     # Read recent messages from every chat
    b3t teams sweep --chat "Leadership" # Only chats matching this substring
    b3t teams sweep --since 14 --json   # Last 14 days, machine-readable
    b3t teams save --dir PATH           # Write teams-*.md submission files

Teams web is a single-page app with no usable read API for personal chats,
so this reads the rendered message list. Each message carries an author
element and an ISO timestamp, which is what makes the parse reliable.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import chatpane
import session
from constants import TEAMS_URL

# Rail entries that are navigation, not conversations.
SKIP_RAIL = {
    "Copilot", "Quick views", "Drafts", "Favorites", "Chats",
    "Teams and channels", "See all channels", "See all your teams",
    "Communities", "Join communities", "Unread", "Channels",
    "Meeting chats", "More", "Activity", "Calendar", "Files",
}


def dispatch(args):
    action = args.action
    if not action:
        print("Usage: b3t teams <login|list|sweep|save>", file=sys.stderr)
        return 2
    if action == "login":
        return cmd_login(args)
    if action == "list":
        return cmd_list(args)
    if action == "sweep":
        return cmd_sweep(args)
    if action == "save":
        return cmd_save(args)
    return 2


def _eval_json(js, timeout=30):
    """Evaluate a page expression returning a JSON string. Returns the object."""
    result = session.run("eval", js, timeout=timeout)
    for line in result.stdout.split("\n"):
        line = line.strip()
        if not line or line.startswith("###") or line.startswith("```"):
            continue
        try:
            parsed = line
            if parsed.startswith('"'):
                parsed = json.loads(parsed)
            return json.loads(parsed) if isinstance(parsed, str) else parsed
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return None


def ensure_authenticated():
    """Make sure a Teams tab is open and signed in."""
    session.ensure_running()
    url = session.current_url() or ""
    if "teams." not in url:
        session.navigate(TEAMS_URL)
        time.sleep(12)
        url = session.current_url() or ""
    if "login.microsoftonline.com" in url or "login.live.com" in url:
        print("ERROR: Teams is not signed in.", file=sys.stderr)
        print("Run: b3t open, sign in to Teams, b3t close", file=sys.stderr)
        return False
    # The rail is the proof the app actually rendered.
    for _ in range(6):
        probe = _eval_json(
            '() => JSON.stringify({n: document.querySelectorAll("[role=treeitem]").length})'
        )
        if probe and probe.get("n"):
            return True
        time.sleep(5)
    print("ERROR: Teams did not finish loading.", file=sys.stderr)
    return False


def _rail_names():
    """Names of every conversation in the left rail, in rail order."""
    js = '''() => JSON.stringify(
  Array.from(document.querySelectorAll("[role=treeitem]"))
    .map(i => (i.innerText || "").split("\\n")[0].trim())
    .filter(Boolean)
)'''
    names = _eval_json(js) or []
    return [n for n in names if n not in SKIP_RAIL]


def _open_chat(name):
    """Click a rail entry by exact name. Returns True when the pane changes."""
    escaped = json.dumps(name)
    code = (
        'async function main(page){'
        f'  const items = await page.locator("[role=treeitem]").all();'
        '  for (const it of items) {'
        '    const t = (await it.innerText()).split("\\n")[0].trim();'
        f'    if (t === {escaped}) {{ await it.click(); await page.waitForTimeout(5000); return "ok"; }}'
        '  }'
        '  return "missing";'
        '}'
    )
    result = session.run("run-code", code, timeout=60)
    return "ok" in (result.stdout or "")


def _scroll_to_bottom():
    """Jump to the newest message. Opening a chat can render mid-history."""
    code = (
        'async function main(page){'
        '  const pane = page.locator("[data-tid=chat-pane-list], [data-tid=message-pane]").first();'
        '  try { await pane.hover({timeout: 3000}); } catch (e) {}'
        '  for (let i=0;i<6;i++) { await page.mouse.wheel(0, 3000); await page.waitForTimeout(500); }'
        '  await page.waitForTimeout(2500);'
        '  return "bottom";'
        '}'
    )
    session.run("run-code", code, timeout=60)


# The scrolling element is the viewport, not the list wrapper; the older
# chat-pane-list / message-pane ids no longer exist in Teams.
PANE = "[data-tid=message-pane-list-viewport], [data-tid=chat-pane-list]"
MAX_SCROLL_STEPS = 60


def _scroll_to_bottom():
    """Pin to the newest message. Returns True only when that is proven."""
    return chatpane.seek_bottom(PANE)


def _read_messages(chat_name):
    """Parse the rendered message list of the open conversation."""
    js = '''() => {
  const items = document.querySelectorAll("[data-tid=chat-pane-item]");
  const out = [];
  items.forEach(it => {
    const authorEl = it.querySelector("[data-tid=message-author-name]");
    if (!authorEl) return;                      // date separators, system rows
    const timeEl = it.querySelector("time");
    const bodyEl = it.querySelector("[data-tid=chat-pane-message]");
    let body = (bodyEl ? bodyEl.innerText : it.innerText) || "";
    body = body.split("\\n")
      .map(s => s.trim())
      .filter(s => s && !/^\\d+ \\w+ reaction\\.?$/.test(s))
      .join("\\n");
    out.push({
      author: (authorEl.innerText || "").trim(),
      ts: timeEl ? timeEl.getAttribute("datetime") : null,
      text: body.trim()
    });
  });
  return JSON.stringify(out);
}'''
    rows = _eval_json(js)
    if rows is None:
        return None          # extraction failed: not the same as "no rows"
    for r in rows:
        r["chat"] = chat_name
    return rows


def _local_stamp(ts):
    """Teams stores UTC in the time element. The newsletter works in local time."""
    if not ts:
        return ""
    try:
        when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts[:16].replace("T", " ")
    return when.astimezone().strftime("%Y-%m-%d %H:%M")


def _read_window(chat_name, attempts=2):
    """Read the rendered window, retrying once before calling it unreadable."""
    for _ in range(attempts):
        rows = _read_messages(chat_name)
        if rows is not None:
            return rows
    return None


def _within_since(rows, since_days):
    if not since_days:
        return rows
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    kept = []
    for r in rows:
        when = _row_dt(r)
        if when is None:
            kept.append(r)          # undated: keep rather than silently drop
        elif when >= cutoff:
            kept.append(r)
    return kept


def _row_dt(row):
    """Parse a row's timestamp, or None when it has none."""
    ts = row.get("ts")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _msg_key(row):
    return (row.get("chat"), row.get("author"), row.get("ts"), row.get("text"))


def _oldest_dt(rows):
    stamps = [d for d in (_row_dt(r) for r in rows) if d]
    return min(stamps) if stamps else None


def _sort_key(row):
    """Chronological order. Undated rows sort last."""
    ts = row.get("ts")
    if not ts:
        return (1, datetime.max.replace(tzinfo=timezone.utc))
    try:
        return (0, datetime.fromisoformat(ts.replace("Z", "+00:00")))
    except ValueError:
        return (1, datetime.max.replace(tzinfo=timezone.utc))


def _dedupe(rows):
    """Teams renders a preview line and the body; collapse exact repeats."""
    seen = set()
    out = []
    for r in rows:
        key = (r.get("chat"), r.get("author"), r.get("ts"), r.get("text"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _gather(args):
    """Shared by sweep and save. Returns a list of message dicts."""
    if not ensure_authenticated():
        return None
    wanted = getattr(args, "chat", None)
    since = getattr(args, "since", None)
    names = _rail_names()
    if wanted:
        names = [n for n in names if wanted.lower() in n.lower()]
        if not names:
            print(f"ERROR: no chat matching '{wanted}'.", file=sys.stderr)
            return None
    cutoff = (datetime.now(timezone.utc) - timedelta(days=since)) if since else None
    rows, partial = [], []
    for name in names:
        print(f"Reading: {name}", file=sys.stderr)
        if not _open_chat(name):
            print(f"  skipped (could not open)", file=sys.stderr)
            continue
        # The list is virtualized: scrolling up unloads the newest rows, so
        # read at every step and let _dedupe merge the overlap.
        at_newest = _scroll_to_bottom()
        read_failures = 0
        found = _read_window(name)
        if found is None:
            read_failures = 1
            found = []
        seen = {_msg_key(r) for r in found}
        # Coverage is only claimed when a boundary is proven: the collected
        # window passes the cutoff, or the pane is measurably at the top of
        # its history. A step budget running out is not proof of either.
        reached_cutoff = False
        reached_top = False
        for _ in range(MAX_SCROLL_STEPS):
            oldest = _oldest_dt(found)
            if cutoff and oldest and oldest < cutoff:
                reached_cutoff = True
                break
            geo = chatpane.step_up(PANE)
            batch = _read_window(name)
            if batch is None:
                # Reaching the top proves where the scroll ended, not that
                # everything on the way was read. An unread window is a hole.
                read_failures += 1
                batch = []
            fresh = [r for r in batch if _msg_key(r) not in seen]
            if fresh:
                seen.update(_msg_key(r) for r in fresh)
                found.extend(fresh)
            if chatpane.at_top(geo):
                reached_top = True
                break
            if geo is None:
                break                    # unmeasurable: coverage unknown
        found = _dedupe(found)
        covered = reached_cutoff or reached_top
        whole = at_newest and covered and read_failures == 0
        why = []
        if not at_newest:
            why.append("did not reach the newest message")
        if not covered:
            why.append("did not reach the cutoff or the top of the history")
        if read_failures:
            why.append(f"{read_failures} window(s) could not be read")
        note = "" if whole else f"  (PARTIAL: {'; '.join(why)})"
        print(f"  {len(found)} message(s){note}", file=sys.stderr)
        if not whole:
            partial.append(name)
        rows.extend(found)
    if partial:
        print(f"WARNING: history not fully covered for: {', '.join(partial)}",
              file=sys.stderr)
        print("The --since window may be incomplete.", file=sys.stderr)
    return sorted(_dedupe(_within_since(rows, since)),
                  key=lambda r: (r["chat"], _sort_key(r)))


def cmd_login(args):
    if not ensure_authenticated():
        return 1
    print("Teams authenticated.")
    return 0


def cmd_list(args):
    if not ensure_authenticated():
        return 1
    names = _rail_names()
    if not names:
        print("No chats or channels found.", file=sys.stderr)
        return 1
    for n in names:
        print(f"  {n}")
    return 0


def cmd_sweep(args):
    rows = _gather(args)
    if rows is None:
        return 1
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("No messages matched.")
        return 0
    current = None
    for r in rows:
        if r["chat"] != current:
            current = r["chat"]
            print(f"\n## {current}")
        stamp = _local_stamp(r.get("ts"))
        print(f"  [{stamp}] {r['author']}: {r['text']}")
    return 0


def _slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:50] or "chat"


def cmd_save(args):
    rows = _gather(args)
    if rows is None:
        return 1
    if not rows:
        print("No messages matched; nothing written.")
        return 0
    out_dir = args.dir
    os.makedirs(out_dir, exist_ok=True)
    by_chat = {}
    for r in rows:
        by_chat.setdefault(r["chat"], []).append(r)
    written = 0
    for chat, msgs in by_chat.items():
        path = os.path.join(out_dir, f"teams-{_slugify(chat)}.md")
        lines = [f"# Teams: {chat}", "", f"Swept {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
        for m in msgs:
            stamp = _local_stamp(m.get("ts"))
            lines.append(f"**{m['author']}** ({stamp})")
            lines.append("")
            lines.append(m["text"])
            lines.append("")
        with open(path, "w") as f:
            f.write("\n".join(lines))
        print(f"  {path}  ({len(msgs)} message(s))", file=sys.stderr)
        written += 1
    print(f"Wrote {written} file(s) to {out_dir}")
    return 0
