"""Outlook email commands — deterministic folder checking with thread expansion.

Usage:
    b3t outlook check                    # List Submissions folder messages
    b3t outlook check --folder Inbox     # List Inbox messages
    b3t outlook read 1                   # Read message #1 (expand full thread)
    b3t outlook draft --file note.md     # Create an unaddressed draft in Drafts
    b3t outlook reply --match Uthraa --file reply.md   # Reply all, saved as a draft
    b3t outlook login                    # Verify M365 auth

Flow:
    check: Navigate to Outlook → click folder in sidebar → parse message list
    read:  Click message → expand conversation → output all messages in thread
"""
import json
import os
import re
import sys
import time

import session
from constants import OUTLOOK_URL


def dispatch(args):
    action = args.action
    if not action:
        print("Usage: b3t outlook <login|check|read|draft|reply>", file=sys.stderr)
        return 2
    if action == "login":
        return cmd_login(args)
    elif action == "check":
        return cmd_check(args)
    elif action == "read":
        return cmd_read(args)
    elif action == "draft":
        return cmd_draft(args)
    elif action == "reply":
        return cmd_reply(args)
    return 2


def _ensure_outlook():
    """Navigate to Outlook, verify auth. Returns True if authenticated."""
    session.ensure_running()
    session.navigate(OUTLOOK_URL)
    time.sleep(4)

    url = session.current_url()
    if url and "login.microsoftonline.com" in url:
        print("ERROR: Not authenticated to M365.", file=sys.stderr)
        print("Run: b3t open, log into outlook.office.com, b3t close", file=sys.stderr)
        return False
    return True


def _find_ref(snapshot_text, test_fn):
    """Find first ref matching test_fn(line) -> bool."""
    for line in snapshot_text.split("\n"):
        if test_fn(line):
            m = re.search(r'\[ref=(\w+)\]', line)
            if m:
                return m.group(1)
    return None


def _suppress_unload_guard():
    """Silence the unsaved-changes prompt for one intended navigation.

    Leaving a dirty composer raises a beforeunload prompt, and that prompt
    blocks every later playwright call with "does not handle the modal
    state", so one draft per session would succeed and the rest would fail.
    The previous handler is kept so normal protection can be put back: this
    is scoped to the navigation, not switched off for the session.
    """
    session.run("eval",
                "() => { if (!('__b3tPrevUnload' in window)) "
                "{ window.__b3tPrevUnload = window.onbeforeunload; } "
                "window.onbeforeunload = null; return true; }",
                timeout=15)


def _restore_unload_guard():
    """Put the page's own unsaved-changes protection back."""
    session.run("eval",
                "() => { if ('__b3tPrevUnload' in window) "
                "{ window.onbeforeunload = window.__b3tPrevUnload; "
                "delete window.__b3tPrevUnload; } return true; }",
                timeout=15)


def _eval_json(js, timeout=20):
    """Run a page expression that returns a JSON string, and parse it.

    playwright-cli hands back the value JSON-encoded, so a string result
    arrives double-encoded. None means the call failed or returned nothing
    usable, which callers must not read as "the page said no".
    """
    result = session.run("--raw", "eval", js, timeout=timeout)
    try:
        value = json.loads((result.stdout or "").strip())
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


def _close_open_composer():
    """Leave a composer that is still open, without losing what it holds.

    Outlook's unsaved-changes prompt is registered as an event listener, so
    clearing `window.onbeforeunload` does not stop it, and an open composer
    arms that prompt for whatever the next command tries to do. A full
    composer has a Close button, which saves. An inline reply has none, so
    the way out is to reload the mailbox and answer the prompt with Leave.

    Leave discards. So it is only taken when there is provably nothing to
    lose: Outlook says the draft is saved, or the composer is empty. A
    composer that is neither is left alone and reported, because the caller
    failing is better than somebody's half-written mail disappearing.

    Returns True when no composer is in the way.
    """
    state = _eval_json(r"""() => {
  const b = document.querySelector('div[aria-label="Message body"][contenteditable="true"]');
  if (!b) return JSON.stringify({open: false});
  const btn = [...document.querySelectorAll('button')]
    .find(e => /^close$/i.test((e.getAttribute('aria-label') || e.textContent || '').trim()));
  const page = document.body.innerText.replace(/\s+/g, ' ');
  const to = (document.querySelector('div[aria-label="To"]')?.innerText || '')
    .replace(/[\u200b\s]+/g, ' ').trim();
  return JSON.stringify({
    open: true,
    hasClose: !!btn,
    saved: /draft saved at /i.test(page),
    empty: !(b.innerText || '').trim() && !to
  });
}""", timeout=15)
    if not isinstance(state, dict) or not state.get("open"):
        return True

    if state.get("hasClose"):
        # Close saves the draft, so this needs no permission.
        session.run("eval", """() => {
  const b = [...document.querySelectorAll('button')]
    .find(e => /^close$/i.test((e.getAttribute('aria-label') || e.textContent || '').trim()));
  if (b) b.click();
  return 'CLOSED';
}""", timeout=15)
        time.sleep(3)
        return True

    if not (state.get("saved") or state.get("empty")):
        print("ERROR: a reply composer is open with unsaved text. Finish or "
              "discard it in Outlook; b3t will not throw it away.",
              file=sys.stderr)
        return False

    session.run("goto", OUTLOOK_URL, timeout=60)
    # Leave: the draft is saved, or there is nothing in it.
    session.run("dialog-accept", timeout=20)
    time.sleep(4)
    return True


def _click_folder(folder_name):
    """Click a folder in the Outlook sidebar.

    An open composer with unsaved text raises a beforeunload prompt on the way
    out, and that prompt blocks every later playwright call with "does not
    handle the modal state". The guard is lifted for this one navigation and
    put straight back.
    """
    # Leave any open composer first: it arms the unsaved-changes prompt, and
    # leaving it can navigate, which would make the refs found below stale.
    if not _close_open_composer():
        return False

    # The folder tree renders after the page itself, so one snapshot taken too
    # early reports a mailbox with no folders in it.
    ref = None
    for _ in range(5):
        snap = session.snapshot()
        if snap:
            ref = _find_ref(snap, lambda l: f'"{folder_name}"' in l and "treeitem" in l.lower())
            if not ref:
                ref = _find_ref(snap, lambda l: folder_name in l and "treeitem" in l.lower())
        if ref:
            break
        time.sleep(2)
    if ref:
        _suppress_unload_guard()
        session.run("click", ref)
        time.sleep(2)
        _restore_unload_guard()
        return True
    return False


def selected_ref(snap):
    """The ref of the message row the reading pane is showing, if any.

    The ref is extracted and returned whole. Testing `ref in line` instead
    accepts a different row whose ref merely starts the same way: `e1` is a
    substring of `e10`, and replying to the wrong person is not an error
    anyone catches by reading the output.
    """
    for line in (snap or "").split("\n"):
        if "[selected]" in line and "option" in line.lower():
            m = re.search(r'\[ref=(\w+)\]', line)
            if m:
                return m.group(1)
    return None


def _parse_messages(snap):
    """Parse message options from a snapshot. Returns list of dicts."""
    messages = []
    for line in snap.split("\n"):
        # Messages appear as option elements in the listbox
        if 'option "' in line.lower() and "ref=" in line:
            m = re.search(r'\[ref=(\w+)\]', line)
            if not m:
                continue
            ref = m.group(1)

            # Extract the full option text
            text_match = re.search(r'option "([^"]*)"', line)
            if not text_match:
                continue
            full_text = text_match.group(1)

            # Parse out components from the option text
            # Pattern: "Collapsed/Expanded SENDER SUBJECT DATE PREVIEW"
            collapsed = "Collapsed" in full_text
            clean = full_text.replace("Collapsed ", "").replace("Expanded ", "")

            # Remove noise
            clean = re.sub(r'No conversations selected$', '', clean).strip()
            clean = re.sub(r'EXTERNAL EMAIL: Use Caution!\s*', '', clean)
            clean = re.sub(r'IMPORTANT:.*?before sending the reply\.\s*', '', clean)

            messages.append({
                "ref": ref,
                "text": clean,
                "collapsed": collapsed,
            })

    return messages


def _sender_addresses():
    """One address per message row, keyed by that row's own text.

    Rows are returned whole. A row carrying more than one address is marked
    ambiguous rather than guessed at, because attaching the wrong address to
    a sender means mailing the wrong person.
    """
    js = '''() => {
  const rows = Array.from(document.querySelectorAll("[role=option], [role=listitem]"));
  const out = [];
  rows.forEach((row, idx) => {
    const titled = Array.from(row.querySelectorAll("[title]"))
      .map(e => e.getAttribute("title"))
      .filter(t => t && t.indexOf("@") !== -1);
    const uniq = Array.from(new Set(titled));
    out.push({
      idx: idx,
      text: (row.innerText || "").replace(/\\s+/g, " ").trim().slice(0, 200),
      addr: uniq.length === 1 ? uniq[0] : null,
      ambiguous: uniq.length > 1
    });
  });
  return JSON.stringify(out);
}'''
    result = session.run("eval", js, timeout=20)
    for line in result.stdout.split("\n"):
        line = line.strip()
        if not line or line.startswith("###") or line.startswith("```"):
            continue
        try:
            parsed = line
            if parsed.startswith('"'):
                parsed = json.loads(parsed)
            data = json.loads(parsed) if isinstance(parsed, str) else parsed
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return []


STATUS_PREFIXES = ("unread", "read", "draft", "collapsed", "expanded")


def _normalise(text):
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _match_core(text):
    """Strip list-state words so a row and a snapshot option can be compared.

    A snapshot option reads "Unread Jane Doe Subject ...", while the rendered
    row starts with the avatar initials. What both carry is the sender, the
    subject and the time.
    """
    core = _normalise(text)
    # _parse_messages strips this banner from the snapshot option, but the
    # rendered row keeps it, sitting between the time and the preview.
    core = re.sub(r"external email:\s*use caution!\s*", "", core)
    changed = True
    while changed:
        changed = False
        for word in STATUS_PREFIXES:
            if core.startswith(word + " "):
                core = core[len(word) + 1:]
                changed = True
    return core


def _attach_addresses(messages):
    """Attach an address to a message only when the pairing is unambiguous.

    A message is matched to a row only if exactly one row corresponds to it
    and exactly one row-address exists. Anything else is left unset: a blank
    is recoverable, a wrong address is not.
    """
    rows = _sender_addresses()
    if not rows:
        return
    for msg in messages:
        needle = _match_core(msg.get("text"))[:50]
        if len(needle) < 12:
            continue                      # too little to identify a row
        hits = [r for r in rows if needle in _match_core(r.get("text"))]
        if len(hits) != 1:
            continue                      # zero or ambiguous row match
        row = hits[0]
        if row.get("ambiguous") or not row.get("addr"):
            continue                      # row itself is ambiguous
        # The same row must not be claimed by two different messages.
        if any(m.get("_row") == row["idx"] for m in messages):
            continue
        msg["address"] = row["addr"]
        msg["_row"] = row["idx"]


def cmd_login(args):
    if _ensure_outlook():
        print("Outlook authenticated.", file=sys.stderr)
        return 0
    return 1


def cmd_check(args):
    """List messages in a folder."""
    if not _ensure_outlook():
        return 1

    folder = args.folder if hasattr(args, "folder") else "Submissions"

    if not _click_folder(folder):
        print(f"ERROR: Could not find folder '{folder}' in sidebar.", file=sys.stderr)
        return 1

    snap = session.snapshot()
    if not snap:
        print("ERROR: Cannot get page snapshot.", file=sys.stderr)
        return 1

    messages = _parse_messages(snap)

    if getattr(args, "addresses", False):
        _attach_addresses(messages)

    print(f"Folder: {folder}", file=sys.stderr)
    print(f"{len(messages)} messages", file=sys.stderr)

    if not messages:
        print(f"No messages in {folder}.")
    else:
        for i, msg in enumerate(messages, 1):
            addr = msg.get("address")
            suffix = f"  <{addr}>" if addr else ""
            print(f"  {i}. {msg['text'][:120]}{suffix}")

    return 0


def parse_reading_pane(snap):
    """Pull From headers, sender addresses, body text and attachments out of
    a reading-pane snapshot.

    Structure: heading/button "From: X" -> attachments listbox ->
    document "Message body". Kept separate from cmd_read so the snapshot
    shapes Outlook actually produces can be tested without a browser.

    Returns (reading_pane, senders, attachments).
    """
    reading_pane = []
    attachments = []
    senders = []
    pending_from = None      # index of the line holding the current From
    in_body = False
    lines = snap.split("\n")
    for idx, line in enumerate(lines):
        # From headers mark a new message in the reading pane. Outlook renders
        # this as a button; it was a heading before the move to
        # outlook.cloud.microsoft, and matching only the heading made every
        # read come back empty.
        if 'heading "From:' in line or 'button "From:' in line:
            in_body = False
            m = re.search(r'From: ([^"]+)"', line)
            if m:
                pending_from = idx
                reading_pane.append(f"\n--- From: {m.group(1)} ---")
            continue

        # Only the line directly beneath a From header carries its address.
        # Without the adjacency check, an address written in the body is
        # mistaken for the sender and that paragraph is eaten. When the line
        # is not an address it must fall through to the parsing below, or a
        # header followed straight by the body swallows the body marker and
        # the message reads as empty.
        if pending_from is not None and idx == pending_from + 1:
            m = re.search(r'generic \[ref=\w+\]:\s*(.+?)<([^<>@\s]+@[^<>\s]+)>', line)
            pending_from = None
            if m and reading_pane:
                reading_pane[-1] = f"\n--- From: {m.group(1).strip()} <{m.group(2)}> ---"
                senders.append(m.group(2))
                continue

        # Attachments: options with file extension + size
        if "option" in line.lower() and re.search(r'\.(png|jpg|jpeg|gif|pdf|docx|xlsx|zip|webp)\b', line, re.IGNORECASE):
            if re.search(r'\d+\s*(KB|MB|GB)', line):
                m = re.search(r'\[ref=(\w+)\]', line)
                name_match = re.search(r'option "([^"]+)"', line)
                if m and name_match:
                    attachments.append({"ref": m.group(1), "name": name_match.group(1)})

        # "Message body" document is where the actual email content lives
        elif 'document "Message body"' in line:
            in_body = True
            pending_from = None

        # Collect body text from generic elements inside Message body
        elif in_body and "generic [ref=" in line:
            # Extract text after "generic [ref=eNNN]: " — may contain quotes
            text_match = re.search(r'generic \[ref=\w+\]:\s*(.+)$', line)
            if text_match:
                text = text_match.group(1).strip().strip('"')
                if text and "EXTERNAL EMAIL" not in text:
                    reading_pane.append(text)
        elif in_body and "- text:" in line:
            text = re.sub(r'^\s*- text:\s*', '', line).strip()
            if text and len(text) > 3:
                reading_pane.append(text)

        # End of message body section (next heading or toolbar)
        elif in_body and ("toolbar" in line
                          or 'heading "From:' in line
                          or 'button "From:' in line):
            in_body = False

    return reading_pane, senders, attachments


def cmd_read(args):
    """Read a specific message by number, expanding the full thread.

    Outputs: subject, sender(s), body text, attachment names.
    Downloads attachments to --dir if specified.
    """
    import shutil

    if not _ensure_outlook():
        return 1

    folder = args.folder if hasattr(args, "folder") and args.folder else "Submissions"
    if not _click_folder(folder):
        print(f"ERROR: Could not find folder '{folder}' in sidebar.", file=sys.stderr)
        return 1

    msg_num = int(args.number) if hasattr(args, "number") and args.number else 1
    output_dir = args.dir if hasattr(args, "dir") and args.dir else None

    snap = session.snapshot()
    if not snap:
        print("ERROR: Cannot get page snapshot.", file=sys.stderr)
        return 1

    messages = _parse_messages(snap)
    if msg_num < 1 or msg_num > len(messages):
        print(f"ERROR: Message #{msg_num} not found. {len(messages)} messages visible.", file=sys.stderr)
        return 1

    target = messages[msg_num - 1]
    # Say which row this is, so a misread shows up in the output itself.
    print(f"=== Row {msg_num} === {target['text'][:120]}")

    # Click the message to select it. A click that lands nowhere leaves the
    # PREVIOUS message in the reading pane, and read would print that one under
    # this number. Confirm the pane shows the row asked for; click once more if not.
    for attempt in range(2):
        session.run("click", target["ref"])
        time.sleep(2)
        for _ in range(4):
            if selected_ref(session.snapshot() or "") == target["ref"]:
                break
            time.sleep(1.5)
        else:
            continue
        break
    else:
        print(f"ERROR: the reading pane is not showing message #{msg_num}; "
              "refusing to print whatever is open.", file=sys.stderr)
        return 1

    # Expand the conversation if collapsed
    if target["collapsed"]:
        snap = session.snapshot()
        expand_ref = _find_ref(snap, lambda l: "Expand conversation" in l and "button" in l.lower())
        if expand_ref:
            session.run("click", expand_ref)
            time.sleep(2)

    # Read the full thread from the snapshot
    snap = session.snapshot()
    if not snap:
        print("ERROR: Cannot get snapshot after expanding.", file=sys.stderr)
        return 1

    # Parse thread messages (listitem elements in expanded conversation)
    thread_msgs = []
    for line in snap.split("\n"):
        if "listitem" in line.lower() and "ref=e" in line:
            text_match = re.search(r'listitem "([^"]*)"', line)
            if text_match:
                text = text_match.group(1)
                text = re.sub(r'EXTERNAL EMAIL: Use Caution!\s*', '', text)
                if text and len(text) > 10:
                    thread_msgs.append(text)

    # Parse reading pane: From headers, body text and attachments.
    reading_pane, senders, attachments = parse_reading_pane(snap)

    # Output thread
    if thread_msgs:
        print("=== Thread ===")
        for i, msg in enumerate(thread_msgs, 1):
            print(f"\n[{i}] {msg}")

    # Output reading pane
    if reading_pane:
        print("\n=== Content ===")
        for line in reading_pane:
            print(line)

    if senders:
        print("\n=== Sender addresses ===")
        for addr in dict.fromkeys(senders):
            print(f"  {addr}")

    # Output attachments
    if attachments:
        print(f"\n=== Attachments ({len(attachments)}) ===")
        for att in attachments:
            print(f"  {att['name']}")

        # Download attachments if --dir specified
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            # A download lands wherever the browser puts it: the CDP Chrome
            # uses ~/Downloads, a playwright-launched one uses its own session
            # directory. Watch both, or the file is reported as never arriving.
            watch = [os.path.join(os.getcwd(), ".playwright-cli"),
                     os.path.expanduser("~/Downloads")]
            existing = {d: set(os.listdir(d)) for d in watch if os.path.isdir(d)}

            for att in attachments:
                # Click attachment to open preview
                session.run("click", att["ref"])
                time.sleep(2)

                # Find and click Download in the preview
                snap2 = session.snapshot()
                # The preview's Download control is a button. It was only ever
                # looked for as a menuitem, so --dir downloaded nothing.
                dl_ref = _find_ref(snap2, lambda l: "Download" in l and (
                    "button" in l.lower() or "menuitem" in l.lower()))
                if dl_ref:
                    session.run("click", dl_ref)
                    time.sleep(3)

                    # Close preview
                    close_ref = _find_ref(session.snapshot() or "", lambda l: "Close" in l and (
                        "button" in l.lower() or "menuitem" in l.lower()))
                    if close_ref:
                        session.run("click", close_ref)
                        time.sleep(1)

            # Move downloaded files to output dir
            moved = 0
            for d, before in existing.items():
                for f in sorted(set(os.listdir(d)) - before):
                    if (f.startswith("page-") or f.endswith((".yml", ".log"))
                            or f.endswith(".crdownload") or f.startswith(".")):
                        continue
                    dst = os.path.join(output_dir, f)
                    shutil.move(os.path.join(d, f), dst)
                    print(f"  Downloaded: {dst}", file=sys.stderr)
                    print(dst)
                    moved += 1
            if not moved:
                print("  WARNING: no attachment file appeared.", file=sys.stderr)

    if not thread_msgs and not reading_pane:
        print("=== Raw ===")
        for line in snap.split("\n"):
            if "- text:" in line:
                text = re.sub(r'^\s*- text:\s*', '', line).strip()
                if text and len(text) > 10:
                    print(f"  {text}")

    return 0


def parse_draft_file(text):
    """Split an email draft file into (subject, body).

    The house format is a `**Subject:**` line, then a `---` rule, then the
    body in markdown. Any `**To:**` line is ignored on purpose: these drafts
    are created unaddressed so the editor decides who gets them.
    """
    subject = ""
    m = re.search(r"^\*\*Subject:\*\*\s*(.+?)\s*$", text, re.M)
    if m:
        subject = m.group(1).strip()

    parts = re.split(r"^---\s*$", text, maxsplit=1, flags=re.M)
    body = parts[1] if len(parts) > 1 else text
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)

    # Outlook's composer is rich text, not markdown. Drop the emphasis markers
    # rather than leaving asterisks in the sent mail; the editor styles it.
    body = re.sub(r"\*\*(.+?)\*\*", r"\1", body)
    body = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", body)
    body = re.sub(r"^#{1,6}\s*", "", body, flags=re.M)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return subject, body.strip()





def cmd_reply(args):
    """Draft a Reply All to a message, in its own thread, without sending.

    Subscribe and unsubscribe answers belong on the request they answer: the
    person sees their own words underneath, and the thread stays searchable
    next time they write in. A new standalone draft loses all of that, which is
    why this command exists and why `ol draft` is only for board mail.

    Like `ol draft`, it stops at a saved draft. Nothing here clicks Send.
    """
    if not os.path.exists(args.file):
        print(f"ERROR: {args.file} not found", file=sys.stderr)
        return 1

    _, body = parse_draft_file(open(args.file).read())
    if not body:
        print("ERROR: the reply body is empty.", file=sys.stderr)
        return 1

    if not _ensure_outlook():
        return 1

    folder = getattr(args, "folder", None) or "Inbox"
    if not _click_folder(folder):
        print(f"ERROR: Could not find folder '{folder}' in sidebar.", file=sys.stderr)
        return 1

    snap = session.snapshot()
    if not snap:
        print("ERROR: Cannot get page snapshot.", file=sys.stderr)
        return 1
    messages = _parse_messages(snap)
    if not messages:
        print(f"ERROR: no messages visible in {folder}.", file=sys.stderr)
        return 1

    match = getattr(args, "match", None)
    if match:
        hits = [m for m in messages if match.lower() in m["text"].lower()]
        # The message list is virtualised: a snapshot only holds the rows that
        # are on screen, so a message further down reads as "not there".
        for _ in range(8):
            if hits:
                break
            session.run("run-code", "--raw",
                        'async function main(page){ const l = page.locator'
                        '("[role=listbox]").first(); const b = await '
                        'l.boundingBox(); if (!b) return "no list"; '
                        'await page.mouse.move(b.x + b.width / 2, '
                        'b.y + b.height / 2); await page.mouse.wheel(0, 600); '
                        'return "ok"; }',
                        timeout=20)
            time.sleep(1)
            snap = session.snapshot() or ""
            messages = _parse_messages(snap)
            hits = [m for m in messages if match.lower() in m["text"].lower()]
        if not hits:
            print(f"ERROR: no message in {folder} matches {match!r}.", file=sys.stderr)
            return 1
        if len(hits) > 1:
            # Replying to the wrong person's thread is not a recoverable
            # mistake, so an ambiguous match stops rather than guesses.
            print(f"ERROR: {len(hits)} messages match {match!r}:", file=sys.stderr)
            for m in hits[:5]:
                print(f"  {m['text'][:90]}", file=sys.stderr)
            return 1
        target = hits[0]
    else:
        n = int(getattr(args, "number", 1) or 1)
        if n < 1 or n > len(messages):
            print(f"ERROR: message #{n} not found. {len(messages)} visible.", file=sys.stderr)
            return 1
        target = messages[n - 1]

    print(f"Replying to: {target['text'][:80]}", file=sys.stderr)
    click = session.run("click", target["ref"])
    if click.returncode != 0:
        print(f"ERROR: could not open that message: "
              f"{(click.stderr or click.stdout or '').strip()[:160]}", file=sys.stderr)
        return 1
    time.sleep(3)

    # A click that lands nowhere (a stale ref, a list that re-rendered) leaves
    # the PREVIOUS message in the reading pane, and everything below here would
    # then reply to the wrong person. Confirm the pane is showing this one, and
    # remember who it is from.
    opened = None
    for _ in range(5):
        snap = session.snapshot() or ""
        if selected_ref(snap) == target["ref"]:
            pane_from = parse_reading_pane(snap)[1]
            opened = pane_from[0] if pane_from else None
            break
        time.sleep(2)
    else:
        print("ERROR: the reading pane is not showing the message that was "
              "asked for; refusing to reply to whatever is open.", file=sys.stderr)
        return 1

    wanted = "Reply all" if getattr(args, "all", True) else "Reply"
    # The reading pane renders its action bar a beat after the row is selected,
    # so a single snapshot reports no Reply all button on a message that has one.
    ref, snap = None, ""
    for _ in range(10):
        snap = session.snapshot() or ""
        ref = _find_ref(snap, lambda l, w=wanted: re.search(
            r'button "%s"' % re.escape(w), l))
        if ref:
            break
        time.sleep(2)
    if not ref:
        if "[Draft]" in target["text"]:
            print("ERROR: this thread already holds a draft reply. Finish or "
                  "discard it first; b3t will not add a second one.",
                  file=sys.stderr)
        else:
            print(f"ERROR: no '{wanted}' button in the reading pane.", file=sys.stderr)
        return 1
    session.run("click", ref)
    time.sleep(4)

    code = (
        "async function main(page){"
        "  const body = " + json.dumps(body) + ";"
        # The reading pane labels the message it is showing "Message body" as
        # well. Only the composer's copy is editable, and picking the other one
        # fails the fill outright.
        "  const b = page.locator('div[aria-label=\"Message body\"][contenteditable=\"true\"]').first();"
        "  await b.waitFor({state: 'visible', timeout: 30000});"
        "  await page.waitForTimeout(1500);"
        # The reply composer takes focus before it is ready to keep what is
        # typed into it, and swallowed a whole body the first time out.
        # Outlook finishes opening a reply by putting the caret in To, and it
        # does that a moment AFTER the body is on screen. Keystrokes sent
        # across that moment end up split between the two fields, and every
        # comma in the stray half becomes a recipient chip. So: wait for focus
        # to settle, then write the body in one operation rather than
        # keystroke by keystroke.
        "  const focus = async () => await page.evaluate(() =>"
        "    (document.activeElement && document.activeElement.getAttribute('aria-label')) || '');"
        "  let last = await focus();"
        "  for (let i = 0; i < 15; i++) {"
        "    await page.waitForTimeout(1000);"
        "    const now = await focus();"
        "    if (now === last) break;"
        "    last = now;"
        "  }"
        "  await b.click();"
        "  await page.waitForTimeout(500);"
        "  await b.fill(body);"
        "  await page.waitForTimeout(1500);"
        "  const typed = (await b.innerText()).trim().length;"
        "  if (!typed) { return JSON.stringify({focus: false}); }"
        "  let saved = '';"
        "  for (let i = 0; i < 30; i++) {"
        "    await page.waitForTimeout(2000);"
        "    const t = await page.evaluate(() => document.body.innerText.replace(/\\s+/g, ' '));"
        "    const m = t.match(/draft saved at [^ ]+ ?[AP]?M?/i);"
        "    if (m) { saved = m[0].trim(); break; }"
        "  }"
        # The recipient chips render a beat after the composer does, and an
        # empty-looking To is a zero-width space, not an empty string.
        "  const recipients = async () => await page.evaluate(() =>"
        "    (document.querySelector('div[aria-label=\"To\"]')?.innerText || '')"
        "      .replace(/[\\u200b\\s]+/g, ' ').trim());"
        "  for (let i = 0; i < 10 && !(await recipients()); i++) {"
        "    await page.waitForTimeout(1000);"
        "  }"
        "  return JSON.stringify(await page.evaluate((sv) => ({"
        "    saved: sv,"
        "    to: (document.querySelector('div[aria-label=\"To\"]')?.innerText || '')"
        "          .replace(/[\\u200b\\s]+/g, ' ').trim(),"
        "    cc: (document.querySelector('div[aria-label=\"Cc\"]')?.innerText || '').trim(),"
        "    len: (document.querySelector('div[aria-label=\"Message body\"][contenteditable=\"true\"]')?.innerText || '').trim().length"
        "  }), saved));"
        "}"
    )
    result = session.run("run-code", "--raw", code, timeout=300)
    try:
        info = json.loads(json.loads((result.stdout or "").strip()))
    except (json.JSONDecodeError, ValueError):
        print(f"ERROR: could not fill the reply: {(result.stdout or '').strip()[:200]}",
              file=sys.stderr)
        return 1

    # The whole point of a reply is that it is addressed. An empty To means
    # this became an orphan draft, which is the thing being fixed here.
    if info.get("focus") is False:
        print("ERROR: the composer never took focus in the message body; "
              "nothing was typed.", file=sys.stderr)
        return 1
    if not info.get("to"):
        print("ERROR: the reply has no recipient; leaving the composer open.",
              file=sys.stderr)
        return 1
    # A recipient is not the RIGHT recipient. The reply must be addressed to
    # whoever sent the message that was opened.
    if opened and opened.lower() not in info.get("to", "").lower():
        print(f"ERROR: this reply is addressed to {info['to']!r}, not to "
              f"{opened}, who sent the message. Leaving the composer open.",
              file=sys.stderr)
        return 1
    # Prose in the recipient list means part of the body was typed into To,
    # where every comma becomes its own chip. Compare against the body itself
    # rather than guessing from length: names and addresses run long too.
    flat_body = " ".join(body.split())
    probe = flat_body[len(flat_body) // 2:][:30].strip()
    if probe and probe in " ".join(info.get("to", "").split()):
        print("ERROR: text from the body landed in the To field; leaving the "
              "composer open so it can be cleared by hand.", file=sys.stderr)
        return 1
    if not info.get("len"):
        print("ERROR: the reply body did not take.", file=sys.stderr)
        return 1
    if not info.get("saved"):
        print("ERROR: Outlook never reported the draft as saved; leaving the "
              "composer open so nothing is lost.", file=sys.stderr)
        return 1

    print(f"  To: {info['to']}", file=sys.stderr)
    if info.get("cc"):
        print(f"  Cc: {info['cc']}", file=sys.stderr)
    print(f"  Body: {info['len']} characters", file=sys.stderr)
    print(f"  Outlook reports: {info['saved']}", file=sys.stderr)

    # Close the composer, never Send. An open composer also leaves an
    # unsaved-changes prompt armed, which blocks the next command.
    _close_open_composer()
    print("Reply draft saved in the thread. It is not sent.", file=sys.stderr)
    return 0


def cmd_draft(args):
    """Create a draft email in Outlook. Never clicks Send.

    Unaddressed by default, so a draft is waiting for the editor to address,
    trim and send. `--to` fills the To line, and the draft is refused unless
    To ends up holding exactly those addresses and nothing else.
    """
    if not os.path.exists(args.file):
        print(f"ERROR: {args.file} not found", file=sys.stderr)
        return 1

    subject, body = parse_draft_file(open(args.file).read())
    if args.subject:
        subject = args.subject
    if not subject:
        print("ERROR: no subject. Add a '**Subject:** ...' line or pass --subject.",
              file=sys.stderr)
        return 1
    if not body:
        print("ERROR: the draft body is empty.", file=sys.stderr)
        return 1

    if not _ensure_outlook():
        return 1

    print(f"Composing draft: {subject}", file=sys.stderr)

    opened = session.run("--raw", "eval", """() => {
  const b = [...document.querySelectorAll('button,[role=button]')]
    .find(e => /^new mail$/i.test((e.getAttribute('aria-label') || e.textContent || '').trim()));
  if (!b) return 'NO_BUTTON';
  b.click();
  return 'CLICKED';
}""", timeout=20)
    if "NO_BUTTON" in (opened.stdout or ""):
        print("ERROR: could not find the New mail button.", file=sys.stderr)
        return 1
    time.sleep(5)

    # The body is typed, not inserted. And the composer is NOT closed until
    # Outlook says it saved: its draft save is debounced, so closing straight
    # after typing loses the body while keeping the subject, which is exactly
    # what shipped an empty draft the first time.
    code = (
        "async function main(page){"
        "  const subj = " + json.dumps(subject) + ";"
        "  const body = " + json.dumps(body) + ";"
        "  const to = " + json.dumps(list(getattr(args, "to", None) or [])) + ";"
        "  const s = page.locator('input[aria-label=\"Subject\"]').first();"
        "  for (const addr of to) {"
        "    const t = page.locator('div[aria-label=\"To\"][contenteditable=\"true\"]').first();"
        "    await t.click(); await page.waitForTimeout(300);"
        "    await page.keyboard.type(addr, {delay: 10}); await page.waitForTimeout(800);"
        "    await page.keyboard.press(';'); await page.waitForTimeout(800);"
        "  }"
        "  await s.click(); await s.fill(subj);"
        # The reading pane is also labelled "Message body"; only the composer's is editable.
        "  const b = page.locator('div[aria-label=\"Message body\"][contenteditable=\"true\"]').first();"
        "  await b.click();"
        "  await page.waitForTimeout(400);"
        "  await b.pressSequentially(body, {delay: 4});"
        "  await page.waitForTimeout(1000);"
        "  await s.click();"                      # blur the body so the edit commits
        "  let saved = '';"
        "  for (let i = 0; i < 30; i++) {"
        "    await page.waitForTimeout(2000);"
        "    const t = await page.evaluate(() => document.body.innerText.replace(/\\s+/g, ' '));"
        "    const m = t.match(/draft saved at [^ ]+ ?[AP]?M?/i);"
        "    if (m) { saved = m[0].trim(); break; }"
        "  }"
        "  return JSON.stringify(await page.evaluate((sv) => ({"
        "    saved: sv,"
        "    subj: document.querySelector('input[aria-label=\"Subject\"]')?.value || '',"
        "    len: (document.querySelector('div[aria-label=\"Message body\"][contenteditable=\"true\"]')?.innerText || '').length,"
        "    to: (document.querySelector('div[aria-label=\"To\"]')?.innerText || '').trim(),"
        # One entry per recipient chip, in order: the address Outlook resolved
        # it to, read from the chip itself rather than guessed from visible
        # text, or null when the chip never resolved to an address (a
        # name-only chip Outlook could not match to a contact).
        "    toChips: [...document.querySelectorAll("
        "      'div[aria-label=\"To\"] [role=\"option\"], div[aria-label=\"To\"] [role=\"listitem\"]')"
        "    ].map(el => {"
        "      const src = el.getAttribute('title') || el.getAttribute('aria-label') || el.textContent || '';"
        "      const m = src.match(/[\\w.+-]+@[\\w-]+(?:\\.[\\w-]+)+/);"
        "      return m ? m[0].toLowerCase() : null;"
        "    }),"
        "    cc: (document.querySelector('div[aria-label=\"Cc\"]')?.innerText || '').trim()"
        "  }), saved));"
        "}"
    )
    result = session.run("run-code", "--raw", code, timeout=300)
    try:
        info = json.loads(json.loads((result.stdout or "").strip()))
    except (json.JSONDecodeError, ValueError):
        print(f"ERROR: could not fill the composer: {(result.stdout or '').strip()[:200]}",
              file=sys.stderr)
        return 1

    wanted = [a.lower() for a in (getattr(args, "to", None) or [])]
    got = info.get("to") or ""
    # Every recipient chip must resolve to a real address before the draft is
    # accepted: a name-only chip ("Bob Jones") that Outlook never matched to
    # an address is not evidence the draft reached anyone in particular, so
    # it fails the check rather than being read as a lucky match. `toChips`
    # holds one entry per chip (None for an unresolved one); an empty To box
    # produces an empty list, never a list holding a lone None.
    chips = info.get("toChips") or []
    resolved = [c.lower() if c else None for c in chips]
    if not wanted:
        # Strict: nothing was asked for, so nothing may have landed in To.
        to_ok = not got and not resolved
    else:
        to_ok = (bool(resolved) and None not in resolved
                 and sorted(resolved) == sorted(wanted))
    if info.get("cc") or not to_ok:
        print(f"ERROR: recipients are not what was asked for (wanted To={wanted}, "
              f"got To={got!r} Cc={info.get('cc')!r}); leaving the composer open.",
              file=sys.stderr)
        return 1
    if wanted:
        print(f"  To: {got}", file=sys.stderr)
    if not info.get("len"):
        print("ERROR: the message body did not take.", file=sys.stderr)
        return 1
    if not info.get("saved"):
        print("ERROR: Outlook never reported the draft as saved; leaving the "
              "composer open so nothing is lost.", file=sys.stderr)
        return 1
    print(f"  Outlook reports: {info['saved']}", file=sys.stderr)

    # Close, not Send.
    closed = session.run("--raw", "eval", """() => {
  const b = [...document.querySelectorAll('button')]
    .find(e => /^close$/i.test((e.getAttribute('aria-label') || e.textContent || '').trim()));
  if (!b) return 'NO_CLOSE';
  b.click();
  return 'CLOSED';
}""", timeout=20)
    if "NO_CLOSE" in (closed.stdout or ""):
        print("WARNING: could not find Close. The composer is still open, but the "
              "draft is already saved.", file=sys.stderr)
    time.sleep(5)

    # Reopen it and read the body back. The save indicator alone is not proof:
    # an earlier version of this command reported success on a draft that
    # turned out to have no body at all.
    verify = (
        "async function main(page){"
        "  const row = page.locator('[role=option]').filter({hasText: " + json.dumps(subject[:40]) + "}).first();"
        "  if (await row.count() === 0) return JSON.stringify({found: false});"
        "  await row.click();"
        "  await page.waitForTimeout(5000);"
        "  return JSON.stringify(await page.evaluate(() => ({"
        "    found: true,"
        "    subj: document.querySelector('input[aria-label=\"Subject\"]')?.value || '',"
        "    len: (document.querySelector('div[aria-label=\"Message body\"]')?.innerText || '').trim().length"
        "  })));"
        "}"
    )
    _click_folder("Drafts")
    time.sleep(3)
    vres = session.run("run-code", "--raw", verify, timeout=120)
    try:
        vinfo = json.loads(json.loads((vres.stdout or "").strip()))
    except (json.JSONDecodeError, ValueError):
        vinfo = {}
    if not vinfo.get("found"):
        print("WARNING: could not find the saved draft in Drafts to verify it. "
              "Open it and check the body before sending.", file=sys.stderr)
    elif not vinfo.get("len"):
        print("ERROR: the draft saved with an EMPTY BODY. Do not send it.", file=sys.stderr)
        return 1
    else:
        print(f"  Verified after reopening: {vinfo['len']} characters of body.",
              file=sys.stderr)

    who = f"To: {info.get('to')}" if wanted else "no recipients"
    print(f"Draft saved to Drafts: \"{subject}\" ({info['len']} chars, {who}).",
          file=sys.stderr)
    print("Nothing was sent. " + ("Review it and send it yourself." if wanted
          else "Address it and send it yourself."), file=sys.stderr)
    return 0
