"""OurSchoolPages CMS archive and site scan commands."""
import os
import re
import sys
import time

import env
import session
from constants import OSP_BASE, OSP_LOGIN, OSP_CREATE_PAGE, OSP_SCAN_PAGES


def dispatch(args):
    action = args.action
    if not action:
        print("Usage: b3t osp <login|archive|scan>", file=sys.stderr)
        return 2
    if action == "login":
        return cmd_login(args)
    elif action == "archive":
        return cmd_archive(args)
    elif action == "scan":
        return cmd_scan(args)
    return 2


def ensure_authenticated():
    """Check OSP auth, auto-login if needed."""
    session.ensure_running()

    # Test by navigating to admin
    session.navigate(f"{OSP_BASE}/Admin/Index")
    time.sleep(2)
    url = session.current_url()
    if url and "/Account/LogOn" not in url:
        return True

    user = os.environ.get("OURSCHOOLPAGES_USER")
    passw = os.environ.get("OURSCHOOLPAGES_PASS")
    if not user or not passw:
        print("ERROR: Set OURSCHOOLPAGES_USER and OURSCHOOLPAGES_PASS in .env", file=sys.stderr)
        return False

    print("Logging in to OurSchoolPages...", file=sys.stderr)
    session.navigate(OSP_LOGIN)
    time.sleep(2)

    snap = session.snapshot()
    if not snap:
        return False

    user_ref = pass_ref = submit_ref = None
    for line in snap.split("\n"):
        ll = line.lower()
        if ("username" in ll or "email" in ll) and "textbox" in ll and not user_ref:
            m = re.search(r'\[ref=(\w+)\]', line)
            if m:
                user_ref = m.group(1)
        if "password" in ll and "textbox" in ll and not pass_ref:
            m = re.search(r'\[ref=(\w+)\]', line)
            if m:
                pass_ref = m.group(1)
        if ("log" in ll or "sign" in ll) and "button" in ll and not submit_ref:
            m = re.search(r'\[ref=(\w+)\]', line)
            if m:
                submit_ref = m.group(1)

    if not user_ref or not pass_ref:
        print("ERROR: Cannot find login fields.", file=sys.stderr)
        return False

    session.run("fill", user_ref, user)
    session.run("fill", pass_ref, passw)

    if submit_ref:
        session.run("click", submit_ref)
    else:
        session.run("press", pass_ref, "Enter")

    time.sleep(3)
    session.save_state()
    print("OurSchoolPages login successful.", file=sys.stderr)
    return True


def cmd_login(args):
    if ensure_authenticated():
        return 0
    return 1


def cmd_archive(args):
    """Create archive page for an edition."""
    if not ensure_authenticated():
        return 1

    html_path = args.html
    if not os.path.exists(html_path):
        print(f"ERROR: File not found: {html_path}", file=sys.stderr)
        return 1

    with open(html_path) as f:
        html_content = f.read()

    edition_date = args.edition
    slug = f"{edition_date}-english"
    heading = f"Bear Tracks - {edition_date}"

    print(f"Creating archive page: {slug}", file=sys.stderr)
    session.navigate(OSP_CREATE_PAGE)
    time.sleep(2)

    snap = session.snapshot()
    if not snap:
        return 1

    # Find form fields: Name, Heading
    name_ref = heading_ref = None
    for line in snap.split("\n"):
        ll = line.lower()
        if "name" in ll and "textbox" in ll and not name_ref:
            m = re.search(r'\[ref=(\w+)\]', line)
            if m:
                name_ref = m.group(1)
        if "heading" in ll and "textbox" in ll and not heading_ref:
            m = re.search(r'\[ref=(\w+)\]', line)
            if m:
                heading_ref = m.group(1)

    if not name_ref:
        print("ERROR: Cannot find Name field.", file=sys.stderr)
        return 1

    session.run("fill", name_ref, slug)
    if heading_ref:
        session.run("fill", heading_ref, heading)

    # For HTML content, need to use Source Code button in TinyMCE
    # Store HTML in localStorage, then inject via TinyMCE API
    session.run("localstorage-set", "_b3t_html", html_content)

    js = '''() => {
  const html = localStorage.getItem("_b3t_html");
  const editor = window.tinymce && window.tinymce.activeEditor;
  if (editor) {
    editor.setContent(html);
    return "ok";
  }
  return "no-tinymce";
}'''
    result = session.run("eval", js)

    session.run("localstorage-set", "_b3t_html", "")

    if "ok" not in result.stdout:
        print("WARNING: Could not inject HTML via TinyMCE. Use Source button manually.", file=sys.stderr)
        print("HTML stored in clipboard-ready format.", file=sys.stderr)

    print(f"Page form filled. Review and save manually.", file=sys.stderr)
    print(f"URL will be: {OSP_BASE}/Page/BearTracks/{slug}")
    return 0


def _parse_page_content(snap):
    """Extract headings, text, and links from a standard page snapshot."""
    lines = []
    for line in snap.split("\n"):
        if "heading" in line.lower() and "level" in line:
            m = re.search(r'heading "([^"]+)"', line)
            if m:
                text = m.group(1).strip()
                if text and len(text) > 3 and "Sign in" not in text:
                    lines.append(f"# {text}")
        elif "- text:" in line:
            text = re.sub(r'^\s*- text:\s*', '', line).strip()
            if text and len(text) > 20:
                lines.append(text)
        elif "link" in line.lower() and "/url:" not in line:
            m = re.search(r'link "([^"]+)"', line)
            if m:
                text = m.group(1).strip()
                if text and len(text) > 10 and "Powered by" not in text:
                    lines.append(f"[link] {text}")

    seen = set()
    unique = []
    for l in lines:
        if l not in seen:
            seen.add(l)
            unique.append(l)
    return "\n".join(unique[:30])


def _parse_calendar(snap):
    """Extract calendar events from month view.

    Structure: alternating date rows and event rows.
    Date row: 7 cells with day numbers (Sun-Sat), e.g. "May 31 Jun 1 2 3 4 5 6"
    Event row: 7 cells, most empty, event text in the column matching its day.
    """
    lines = snap.split("\n")
    events = []
    current_month = ""

    # Get month/year
    for line in lines:
        if "generic [ref=" in line:
            m = re.search(r':\s*((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})', line)
            if m:
                current_month = m.group(1).strip()
                break

    # Parse rows — track date rows and map column positions to dates
    current_dates = []  # 7 dates for current week (Sun-Sat)
    for line in lines:
        # Date row: row text contains the day numbers
        row_match = re.search(r'row "(.+?)" \[ref=', line)
        if row_match:
            row_text = row_match.group(1)
            # Check if this is a date row (contains mostly numbers)
            parts = row_text.split()
            dates_in_row = []
            i = 0
            while i < len(parts):
                # Handle "Jun 1" or "Jul 1" style
                if re.match(r'^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$', parts[i]) and i + 1 < len(parts):
                    dates_in_row.append(f"{parts[i]} {parts[i+1]}")
                    i += 2
                elif re.match(r'^\d{1,2}$', parts[i]):
                    dates_in_row.append(parts[i])
                    i += 1
                else:
                    # Not a date row — it's an event row
                    break
            if len(dates_in_row) == 7:
                current_dates = dates_in_row
            continue

        # Event cells within event rows — find non-empty cells by position
        cell_match = re.search(r'cell "([^"]+)"', line)
        if cell_match and current_dates:
            cell_text = cell_match.group(1)
            # Skip if it's just a date number
            if re.match(r'^(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+)?\d{1,2}$', cell_text):
                continue
            # This is an event — figure out which column it's in
            # Count preceding empty cells + this cell to determine column
            # Since we can't easily get column from snapshot, use a different approach:
            # The event row has the event name in its row header text
            # Just pair it with the date row above and find which column has content
            # For simplicity, count cell occurrences in this event row
            events.append(cell_text)

    # Better approach: parse event rows by matching column position
    # Re-parse using row-level logic
    events_with_dates = []
    current_dates = []
    in_event_row = False
    col_idx = 0

    for line in lines:
        row_match = re.search(r'row "(.*?)" \[ref=', line)
        if row_match:
            row_text = row_match.group(1)
            # Check if date row
            parts = row_text.split()
            dates_in_row = []
            i = 0
            while i < len(parts):
                if re.match(r'^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$', parts[i]) and i + 1 < len(parts):
                    dates_in_row.append(f"{parts[i]} {parts[i+1]}")
                    i += 2
                elif re.match(r'^\d{1,2}$', parts[i]):
                    dates_in_row.append(parts[i])
                    i += 1
                else:
                    break
            if len(dates_in_row) == 7:
                current_dates = dates_in_row
                in_event_row = False
            else:
                # Event row
                in_event_row = True
                col_idx = 0
            continue

        if in_event_row and "cell" in line:
            cell_match = re.search(r'cell "([^"]*)"', line)
            if cell_match:
                cell_text = cell_match.group(1)
                if cell_text and not re.match(r'^\d{1,2}$', cell_text):
                    # Found an event
                    date_label = current_dates[col_idx] if col_idx < len(current_dates) else "?"
                    events_with_dates.append(f"  {date_label}: {cell_text}")
            # Empty cell match (no text)
            elif re.search(r'cell \[ref=', line):
                pass
            col_idx += 1

    header = f"Month: {current_month}" if current_month else "Calendar Events"
    if events_with_dates:
        return header + "\n" + "\n".join(events_with_dates)
    return header + "\n  (no events)"


def _scan_pages():
    """Parse OSP_SCAN_PAGES env: 'Name|/path,Name2|/path2'. Defaults to home only."""
    if OSP_SCAN_PAGES:
        pages = []
        for part in OSP_SCAN_PAGES.split(","):
            part = part.strip()
            if "|" in part:
                name, path = part.split("|", 1)
                pages.append((name.strip(), path.strip()))
            elif part:
                pages.append((part, part if part.startswith("/") else f"/{part}"))
        if pages:
            return pages
    return [("Home", "/")]


def cmd_scan(args):
    """Scan configured site pages for content updates. No auth needed."""
    if not OSP_BASE:
        print("ERROR: OSP_BASE must be set in .env", file=sys.stderr)
        return 1

    session.ensure_running()
    scan_pages = _scan_pages()

    print(f"Scanning {OSP_BASE} ({len(scan_pages)} pages)...", file=sys.stderr)
    results = []

    for name, path in scan_pages:
        url = f"{OSP_BASE}{path}"
        session.navigate(url)
        time.sleep(2)

        snap = session.snapshot()
        if not snap:
            results.append({"page": name, "url": url, "content": "[ERROR: no snapshot]"})
            continue

        # Calendar pages need special parsing
        if name == "Calendar":
            content = _parse_calendar(snap)
        else:
            content = _parse_page_content(snap)

        results.append({"page": name, "url": url, "content": content})

    # Output
    for r in results:
        print(f"\n{'='*60}")
        print(f"PAGE: {r['page']}")
        print(f"URL: {r['url']}")
        print(f"{'─'*60}")
        if r["content"]:
            print(r["content"])
        else:
            print("(empty or no text content)")

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Scanned {len(results)} pages.", file=sys.stderr)
    return 0
