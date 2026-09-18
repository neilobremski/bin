"""WhatsApp Web chat sweeping.

Usage:
    b3t whatsapp login                  # Report link status
    b3t whatsapp list                   # List chats in the sidebar
    b3t whatsapp sweep                  # Read recent messages from every chat
    b3t whatsapp sweep --chat "Board"   # Only chats matching this substring
    b3t whatsapp sweep --since 14 --json
    b3t whatsapp save --dir PATH        # Write whatsapp-*.md submission files

WhatsApp Web has no API. This reads the rendered chat, which is workable
because every message bubble carries a `data-pre-plain-text` attribute of
the form "[H:MM AM, M/D/YYYY] Sender: ".

Linking the browser needs a QR code scanned from the phone. No command can
do that; `login` reports whether it is still linked so a session fails loudly
rather than returning an empty sweep.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

import chatpane
import session
from constants import WHATSAPP_URL

# "[6:59 AM, 9/16/2026] +1 (206) 291-2692: "
PRE_PLAIN = re.compile(r"^\[(.+?),\s*(\d{1,2}/\d{1,2}/\d{4})\]\s*(.*?):\s*$")


def dispatch(args):
    action = args.action
    if not action:
        print("Usage: b3t whatsapp <login|list|sweep|save>", file=sys.stderr)
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


def _link_state():
    """Returns 'linked', 'qr' (needs a scan), or 'loading'."""
    probe = _eval_json('''() => JSON.stringify({
  pane: !!document.querySelector("#pane-side"),
  canvas: !!document.querySelector("canvas"),
  rows: document.querySelectorAll("#pane-side [role=row]").length
})''')
    if not probe:
        return "loading"
    if probe.get("pane"):
        return "linked"
    if probe.get("canvas"):
        return "qr"
    return "loading"


def ensure_linked():
    """Make sure WhatsApp Web is open and linked to the phone."""
    session.ensure_running()
    url = session.current_url() or ""
    if "web.whatsapp.com" not in url:
        session.navigate(WHATSAPP_URL)
        time.sleep(10)
    for _ in range(6):
        state = _link_state()
        if state == "linked":
            return True
        if state == "qr":
            print("ERROR: WhatsApp Web is not linked.", file=sys.stderr)
            print("A human must scan the QR code:", file=sys.stderr)
            print("  1. b3t open", file=sys.stderr)
            print("  2. go to web.whatsapp.com, leave 'Stay logged in' ticked", file=sys.stderr)
            print("  3. scan the code from the phone, then b3t close", file=sys.stderr)
            return False
        time.sleep(5)
    print("ERROR: WhatsApp Web did not finish loading.", file=sys.stderr)
    return False


def _chat_names():
    js = '''() => JSON.stringify(
  Array.from(document.querySelectorAll("#pane-side [role=row]")).map(r => {
    const t = r.querySelector("span[title]");
    return t ? t.getAttribute("title").trim() : "";
  }).filter(Boolean)
)'''
    return _eval_json(js) or []


def _open_chat(name):
    escaped = json.dumps(name)
    code = (
        'async function main(page){'
        f'  const t = page.locator("#pane-side span[title=" + JSON.stringify({escaped}) + "]").first();'
        '  if (await t.count() === 0) return "missing";'
        '  await t.click();'
        '  await page.waitForTimeout(4000);'
        '  return "ok";'
        '}'
    )
    result = session.run("run-code", code, timeout=60)
    return "ok" in (result.stdout or "")


def _scroll_to_bottom():
    """Jump to the newest message. Opening a chat can render mid-history."""
    code = (
        'async function main(page){'
        '  const pane = page.locator("#main [data-testid=conversation-panel-messages], #main").first();'
        '  try { await pane.hover({timeout: 3000}); } catch (e) {}'
        '  for (let i=0;i<6;i++) { await page.mouse.wheel(0, 3000); await page.waitForTimeout(500); }'
        '  await page.waitForTimeout(2500);'
        '  return "bottom";'
        '}'
    )
    session.run("run-code", code, timeout=60)


PANE = "#main [data-testid=conversation-panel-messages], #main"
MAX_SCROLL_STEPS = 60


def _scroll_to_bottom():
    """Pin to the newest message. Returns True only when that is proven."""
    return chatpane.seek_bottom(PANE)


def _read_messages(chat_name):
    """Parse message bubbles of the open chat."""
    js = '''() => {
  const bubbles = document.querySelectorAll("[data-pre-plain-text]");
  const out = [];
  bubbles.forEach(b => {
    const meta = b.getAttribute("data-pre-plain-text") || "";
    const row = b.closest("[role=row]") || b.parentElement;
    // A reply renders the quoted message inside the same bubble, above the
    // real text. Taking the first match attributes someone else's words to
    // the replier, so drop anything inside a quote and keep the last block.
    const isQuoted = el => {
      for (let e = el; e && e !== b; e = e.parentElement) {
        const t = (e.getAttribute("data-testid") || "").toLowerCase();
        const a = (e.getAttribute("aria-label") || "").toLowerCase();
        if (t.includes("quoted") || a.includes("quoted")) return true;
      }
      return false;
    };
    let scope = b.querySelectorAll("[data-testid=selectable-text]").length
      ? b : (row || b);
    const texts = Array.from(scope.querySelectorAll("[data-testid=selectable-text]"))
      .filter(el => !isQuoted(el));
    const textEl = texts.length ? texts[texts.length - 1] : null;
    let body = (textEl ? textEl.innerText : b.innerText) || "";
    // The display name is the first line of the bubble when the sender is
    // not the signed-in user; data-pre-plain-text only carries the number.
    let display = "";
    if (row) {
      const labelled = Array.from(row.querySelectorAll("[aria-label]"))
        .map(e => e.getAttribute("aria-label"))
        .find(l => l && l.startsWith("Maybe "));
      if (labelled) display = labelled.replace(/^Maybe /, "").trim();
      if (!display) {
        const first = (row.innerText || "").split("\\n")[0].trim();
        if (first && !body.startsWith(first)) display = first;
      }
    }
    out.push({meta: meta, display: display, text: body.trim()});
  });
  return JSON.stringify(out);
}'''
    rows = _eval_json(js)
    if rows is None:
        return None          # extraction failed: not the same as "no rows"
    parsed = []
    for r in rows:
        m = PRE_PLAIN.match((r.get("meta") or "").strip())
        clock, day, sender = ("", "", "")
        if m:
            clock, day, sender = m.group(1), m.group(2), m.group(3)
        author = r.get("display") or sender or "(unknown)"
        parsed.append({
            "chat": chat_name,
            "author": author.strip(),
            "phone": sender.strip(),
            "date": day,
            "time": clock,
            "text": (r.get("text") or "").strip(),
        })
    return parsed


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
    cutoff = datetime.now() - timedelta(days=since_days)
    kept = []
    for r in rows:
        when = _row_dt(r)
        if when is None:
            kept.append(r)          # unparseable: keep rather than drop
        elif when >= cutoff:
            kept.append(r)
    return kept


def _row_dt(row):
    """Parse date + time into a datetime, or None when unparseable.

    The time matters: judging a message by its date alone drops everything
    posted on the cutoff day, which for --since 1 is most of what is wanted.
    """
    day, clock = row.get("date"), row.get("time")
    if not day:
        return None
    for fmt in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M"):
        try:
            return datetime.strptime(f"{day} {clock}", fmt)
        except ValueError:
            continue
    try:
        # Date with no usable time: treat as end of that day so a message is
        # never dropped for a clock we could not read.
        return datetime.strptime(day, "%m/%d/%Y").replace(hour=23, minute=59)
    except ValueError:
        return None


def _msg_key(row):
    return (row["chat"], row["author"], row["date"], row["time"], row["text"])


def _oldest_dt(rows):
    stamps = [d for d in (_row_dt(r) for r in rows) if d]
    return min(stamps) if stamps else None


def _sort_key(row):
    """Chronological order. Undated rows sort last."""
    when = _row_dt(row)
    return (1, datetime.max) if when is None else (0, when)


def _dedupe(rows):
    seen = set()
    out = []
    for r in rows:
        key = _msg_key(r)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _gather(args):
    if not ensure_linked():
        return None
    wanted = getattr(args, "chat", None)
    since = getattr(args, "since", None)
    names = _chat_names()
    if wanted:
        names = [n for n in names if wanted.lower() in n.lower()]
        if not names:
            print(f"ERROR: no chat matching '{wanted}'.", file=sys.stderr)
            return None
    cutoff = (datetime.now() - timedelta(days=since)) if since else None
    rows, partial = [], []
    for name in names:
        print(f"Reading: {name}", file=sys.stderr)
        if not _open_chat(name):
            print("  skipped (could not open)", file=sys.stderr)
            continue
        # The message list is virtualized: scrolling up unloads the newest
        # bubbles, so read at every step and let _dedupe merge the overlap.
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
    session.ensure_running()
    url = session.current_url() or ""
    if "web.whatsapp.com" not in url:
        session.navigate(WHATSAPP_URL)
        time.sleep(10)
    state = _link_state()
    if state == "linked":
        print("WhatsApp Web is linked.")
        return 0
    if state == "qr":
        print("WhatsApp Web needs a QR scan from the phone.", file=sys.stderr)
        return 1
    print("WhatsApp Web did not finish loading.", file=sys.stderr)
    return 1


def cmd_list(args):
    if not ensure_linked():
        return 1
    names = _chat_names()
    if not names:
        print("No chats found.", file=sys.stderr)
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
        print(f"  [{r['date']} {r['time']}] {r['author']}: {r['text']}")
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
        path = os.path.join(out_dir, f"whatsapp-{_slugify(chat)}.md")
        lines = [f"# WhatsApp: {chat}", "", f"Swept {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
        for m in msgs:
            lines.append(f"**{m['author']}** ({m['date']} {m['time']})")
            lines.append("")
            lines.append(m["text"])
            lines.append("")
        with open(path, "w") as f:
            f.write("\n".join(lines))
        print(f"  {path}  ({len(msgs)} message(s))", file=sys.stderr)
        written += 1
    print(f"Wrote {written} file(s) to {out_dir}")
    return 0
