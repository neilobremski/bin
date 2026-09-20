"""OurSchoolPages CMS archive and site scan commands."""
import json
import os
import re
import sys
import time

import archive
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

    # The form's fields are named, so address them by name. Reading them out of
    # the accessibility snapshot failed twice over: OSP puts each label on its
    # own line above its box, so no one line says both "email" and "textbox",
    # and the Google Translate widget adds five more text inputs to the form
    # for a snapshot scan to pick from. The submit control is an image button.
    fields = session.run("eval", """() => {
  const u = document.querySelector('#EmailAddress, input[name=EmailAddress]');
  const p = document.querySelector('#Password, input[name=Password]');
  const s = document.querySelector('form input[type=image], form button[type=submit], form input[type=submit]');
  return 'OSPFORM:' + (u ? 'U' : '-') + (p ? 'P' : '-') + (s ? 'S' : '-');
}""")
    # A plain token, because `eval` hands back a JSON-escaped string and a
    # check written against the unescaped shape silently never matches.
    found = re.search(r'OSPFORM:(...)', fields.stdout or "")
    if not found or found.group(1)[:2] != "UP":
        print("ERROR: the sign-in form is not the one this expects; "
              f"look at {OSP_LOGIN}.", file=sys.stderr)
        return False

    # `run-code` wraps its argument as `(async function main(page){...})(page)`,
    # so it takes a whole function, not a bare body. A body parses as garbage.
    session.run("run-code", """async function main(page) {
  await page.fill('#EmailAddress', %s);
  await page.fill('#Password', %s);
  const submit = page.locator('form input[type=image], form button[type=submit], form input[type=submit]').first();
  if (await submit.count()) { await submit.click(); }
  else { await page.press('#Password', 'Enter'); }
  await page.waitForTimeout(3000);
}""" % (json.dumps(user), json.dumps(passw)), timeout=60)

    # Say whether it worked. Reporting a login that did not happen sends every
    # command after this one into a sign-in page and blames them for it.
    session.navigate(f"{OSP_BASE}/Admin/Index")
    time.sleep(2)
    url = session.current_url()
    if url and "/Account/LogOn" in url:
        print("ERROR: OurSchoolPages rejected the sign-in. Check "
              "OURSCHOOLPAGES_USER and OURSCHOOLPAGES_PASS.", file=sys.stderr)
        return False

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
    slug = archive.page_slug(edition_date)

    # `gb archive` leaves the page's title beside the HTML. Retyping it here
    # from the date alone produced "Bear Tracks - 2026-09-20", which matches no
    # other page on the site and reads as a filename in the archive listing.
    heading = getattr(args, "title", None)
    meta_path = html_path + ".meta.json"
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
        heading = heading or meta.get("title")
        if meta.get("edition") and meta["edition"] != edition_date:
            print(f"ERROR: {meta_path} is for the {meta['edition']} edition, "
                  f"not {edition_date}.", file=sys.stderr)
            return 1
    if not heading:
        print(f"ERROR: no title. Either generate the page with `b3t gb archive "
              f"--edition {edition_date} --id UUID`, which writes "
              f"{os.path.basename(meta_path)} beside the HTML, or pass --title.",
              file=sys.stderr)
        return 1

    # A page at this address already holds an edition. Creating a second one
    # would either fail or leave two pages fighting over the same URL.
    session.navigate(f"{OSP_BASE}/Page/BearTracks/{slug}")
    time.sleep(2)
    landed = session.current_url() or ""
    if "Error404" not in landed and slug in landed:
        print(f"ERROR: {OSP_BASE}/Page/BearTracks/{slug} already exists. "
              "Edit it in the CMS, or archive a different edition.",
              file=sys.stderr)
        return 1

    print(f"Creating archive page: {slug}", file=sys.stderr)
    session.navigate(OSP_CREATE_PAGE)
    time.sleep(3)

    # The form's fields are named. An earlier version hunted for them in the
    # accessibility snapshot by looking for a line with both "name" and
    # "textbox" in it, and found neither field, because OSP puts every label on
    # its own line above its box.
    session.run("run-code", """async function main(page) {
  await page.fill('#PageName', %s);
  await page.fill('#PageHeading', %s);
}""" % (json.dumps(slug), json.dumps(heading)), timeout=60)

    # The HTML goes in through TinyMCE, which rewrites it on the way: inline
    # styles come off and `style="width:580px"` becomes `width="580"`. That is
    # not damage, it is what every archive page on the site already looks like.
    # It also means the content that lands is shorter than the file, so the
    # length is reported rather than compared.
    session.run("localstorage-set", "_b3t_html", html_content)
    result = session.run("eval", """() => {
  const html = localStorage.getItem("_b3t_html");
  if (!html) return "ARCHIVE:no-html";
  const ed = (window.tinymce && (tinymce.get("Html") || tinymce.activeEditor));
  if (!ed) return "ARCHIVE:no-tinymce";
  ed.setContent(html);
  ed.save();
  const t = document.getElementById("Html");
  return "ARCHIVE:ok:" + (t ? t.value.length : 0) + ":"
    + (ed.getContent().match(/<div class="u-row-container"/g) || []).length;
}""", timeout=60)
    session.run("localstorage-set", "_b3t_html", "")

    state = re.search(r"ARCHIVE:(\w+)(?::(\d+):(\d+))?", result.stdout or "")
    if not state or state.group(1) != "ok":
        print("ERROR: the editor did not take the HTML (%s). Nothing was saved."
              % (state.group(1) if state else "no answer"), file=sys.stderr)
        return 1

    stored, kept = int(state.group(2)), int(state.group(3))
    expected = html_content.count('<div class="u-row-container"')
    print(f"Filled: {stored:,} chars in the editor, {kept} of {expected} rows.",
          file=sys.stderr)
    if kept != expected:
        print(f"ERROR: the editor kept {kept} rows out of {expected}. "
              "Not saving a page that lost content.", file=sys.stderr)
        return 1

    folder = session.run("eval", """() => {
  const d = document.getElementById("PageFolderDropDown");
  return "FOLDER:" + (d ? d.options[d.selectedIndex].text : "none");
}""")
    if "BearTracks" not in (folder.stdout or ""):
        print("ERROR: the page is not filed under BearTracks. Not saving.",
              file=sys.stderr)
        return 1

    if not getattr(args, "save", False):
        print(f'Title: {heading}', file=sys.stderr)
        print("The form is filled and NOT saved. Review it in the browser and "
              "click Save, or re-run with --save.", file=sys.stderr)
        print(f"URL will be: {OSP_BASE}/Page/BearTracks/{slug}")
        return 0

    session.run("run-code", """async function main(page) {
  await page.click('#SaveButton');
  await page.waitForTimeout(5000);
}""", timeout=90)

    session.navigate(f"{OSP_BASE}/Page/BearTracks/{slug}")
    time.sleep(3)
    live = session.current_url() or ""
    if "Error404" in live or slug not in live:
        print(f"ERROR: after saving, {OSP_BASE}/Page/BearTracks/{slug} is not "
              "there. Check the CMS by hand.", file=sys.stderr)
        return 1

    print(f"Published: {OSP_BASE}/Page/BearTracks/{slug}", file=sys.stderr)
    print("The archive listing page is separate and still needs its entry.",
          file=sys.stderr)
    print(f"{OSP_BASE}/Page/BearTracks/{slug}")
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
