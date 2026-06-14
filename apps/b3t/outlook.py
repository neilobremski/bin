"""Outlook email commands — deterministic folder checking with thread expansion.

Usage:
    b3t outlook check                    # List Submissions folder messages
    b3t outlook check --folder Inbox     # List Inbox messages
    b3t outlook read 1                   # Read message #1 (expand full thread)
    b3t outlook login                    # Verify M365 auth

Flow:
    check: Navigate to Outlook → click folder in sidebar → parse message list
    read:  Click message → expand conversation → output all messages in thread
"""
import os
import re
import sys
import time

import session
from constants import OUTLOOK_URL


def dispatch(args):
    action = args.action
    if not action:
        print("Usage: b3t outlook <login|check|read>", file=sys.stderr)
        return 2
    if action == "login":
        return cmd_login(args)
    elif action == "check":
        return cmd_check(args)
    elif action == "read":
        return cmd_read(args)
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


def _click_folder(folder_name):
    """Click a folder in the Outlook sidebar."""
    snap = session.snapshot()
    if not snap:
        return False

    # Look for treeitem with the folder name
    ref = _find_ref(snap, lambda l: f'"{folder_name}"' in l and "treeitem" in l.lower())
    if not ref:
        # Fallback: look for generic with folder name inside a treeitem context
        ref = _find_ref(snap, lambda l: folder_name in l and "treeitem" in l.lower())
    if ref:
        session.run("click", ref)
        time.sleep(2)
        return True
    return False


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

    print(f"Folder: {folder}", file=sys.stderr)
    print(f"{len(messages)} messages", file=sys.stderr)

    if not messages:
        print(f"No messages in {folder}.")
    else:
        for i, msg in enumerate(messages, 1):
            print(f"  {i}. {msg['text'][:120]}")

    return 0


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

    # Click the message to select it
    session.run("click", target["ref"])
    time.sleep(2)

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

    # Parse reading pane: From headers, message bodies, attachments
    # Structure: heading "From: X" → attachments listbox → document "Message body"
    reading_pane = []
    attachments = []
    in_body = False
    for line in snap.split("\n"):
        # From headers mark a new message in the reading pane
        if 'heading "From:' in line:
            in_body = False
            m = re.search(r'From: ([^"]+)"', line)
            if m:
                reading_pane.append(f"\n--- From: {m.group(1)} ---")

        # Attachments: options with file extension + size
        elif "option" in line.lower() and re.search(r'\.(png|jpg|jpeg|gif|pdf|docx|xlsx|zip|webp)\b', line, re.IGNORECASE):
            if re.search(r'\d+\s*(KB|MB|GB)', line):
                m = re.search(r'\[ref=(\w+)\]', line)
                name_match = re.search(r'option "([^"]+)"', line)
                if m and name_match:
                    attachments.append({"ref": m.group(1), "name": name_match.group(1)})

        # "Message body" document is where the actual email content lives
        elif 'document "Message body"' in line:
            in_body = True

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
        elif in_body and ("toolbar" in line or 'heading "From:' in line):
            in_body = False

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

    # Output attachments
    if attachments:
        print(f"\n=== Attachments ({len(attachments)}) ===")
        for att in attachments:
            print(f"  {att['name']}")

        # Download attachments if --dir specified
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            pw_dir = os.path.join(os.getcwd(), ".playwright-cli")
            existing = set(os.listdir(pw_dir)) if os.path.isdir(pw_dir) else set()

            for att in attachments:
                # Click attachment to open preview
                session.run("click", att["ref"])
                time.sleep(2)

                # Find and click Download in the preview
                snap2 = session.snapshot()
                dl_ref = _find_ref(snap2, lambda l: "Download" in l and "menuitem" in l.lower())
                if dl_ref:
                    session.run("click", dl_ref)
                    time.sleep(3)

                    # Close preview
                    close_ref = _find_ref(session.snapshot() or "", lambda l: "Close" in l and "menuitem" in l.lower())
                    if close_ref:
                        session.run("click", close_ref)
                        time.sleep(1)

            # Move downloaded files to output dir
            if os.path.isdir(pw_dir):
                current = set(os.listdir(pw_dir))
                new_files = [f for f in (current - existing)
                             if not f.startswith("page-") and not f.endswith(".yml") and not f.endswith(".log")]
                for f in new_files:
                    src = os.path.join(pw_dir, f)
                    dst = os.path.join(output_dir, f)
                    shutil.move(src, dst)
                    print(f"  Downloaded: {dst}", file=sys.stderr)
                    print(dst)

    if not thread_msgs and not reading_pane:
        print("=== Raw ===")
        for line in snap.split("\n"):
            if "- text:" in line:
                text = re.sub(r'^\s*- text:\s*', '', line).strip()
                if text and len(text) > 10:
                    print(f"  {text}")

    return 0
