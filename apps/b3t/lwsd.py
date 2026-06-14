"""LWSD school website scanning. No auth needed.

Usage:
    b3t lwsd scan    # Scan both rms.lwsd.org and lwsd.org for events and news
"""
import re
import sys
import time

import session
from constants import LWSD_SCHOOL_URL, LWSD_DISTRICT_URL

DISTRICT_URL = LWSD_DISTRICT_URL


def dispatch(args):
    action = args.action
    if not action:
        print("Usage: b3t lwsd <scan>", file=sys.stderr)
        return 2
    if action == "scan":
        return cmd_scan(args)
    return 2


def cmd_scan(args):
    """Scan school and district sites for events and news. No auth needed."""
    session.ensure_running()

    rms_events = []
    rms_news = []

    if LWSD_SCHOOL_URL:
        session.navigate(LWSD_SCHOOL_URL)
        time.sleep(3)
        snap = session.snapshot()
        rms_events = _parse_events(snap, "School events") if snap else []
        rms_news = _parse_news(snap) if snap else []
    else:
        print("LWSD_SCHOOL_URL not set — skipping school site scan.", file=sys.stderr)

    # Scan district site
    session.navigate(DISTRICT_URL)
    time.sleep(3)
    snap = session.snapshot()

    district_events = _parse_events(snap, "lwsd events") if snap else []
    district_news = _parse_district_news(snap) if snap else []

    # Output
    print("=== School Events ===" if LWSD_SCHOOL_URL else "=== School Events (skipped) ===")
    if rms_events:
        for e in rms_events:
            print(f"  {e}")
    else:
        print("  (none found)")

    print("\n=== School News ===" if LWSD_SCHOOL_URL else "\n=== School News (skipped) ===")
    if rms_news:
        for n in rms_news:
            print(f"  {n}")
    else:
        print("  (none found)")

    print("\n=== LWSD District Events ===")
    if district_events:
        for e in district_events:
            print(f"  {e}")
    else:
        print("  (none found)")

    print("\n=== LWSD District News ===")
    if district_news:
        for n in district_news:
            print(f"  {n}")
    else:
        print("  (none found)")

    print(f"\nScanned: {LWSD_SCHOOL_URL or '(no school URL)'} + {DISTRICT_URL}", file=sys.stderr)
    return 0


def _parse_events(snap, section_heading="RMS events"):
    """Parse events from an events section on the homepage."""
    events = []
    lines = snap.split("\n")
    in_events = False
    current_month = ""
    current_day = ""

    for line in lines:
        # Start of events section
        if f'heading "{section_heading}"' in line:
            in_events = True
            continue
        # End of events section (next major section)
        if in_events and 'heading "' in line and "level=2" in line and section_heading not in line:
            # Check it's not an event button masquerading as a heading
            if "events" not in line.lower() and "calendar" not in line.lower():
                break
        if not in_events:
            continue

        # Month (in time elements or generic elements)
        if "generic [ref=" in line:
            m = re.search(r'generic \[ref=\w+\]:\s*"?([^"]+)"?', line)
            if m:
                text = m.group(1).strip().strip('"')
                # Month abbreviation
                if text in ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"):
                    current_month = text
                # Day number
                elif re.match(r'^\d{1,2}$', text):
                    current_day = text

        # Event title (in button or group elements)
        if ("button" in line or "group" in line) and "ref=" in line:
            m = re.search(r'(?:button|group) "([^"]+)"', line)
            if m:
                title = m.group(1).strip()
                if title and len(title) > 5 and "ref=" not in title:
                    date_str = f"{current_month} {current_day}" if current_month and current_day else ""
                    events.append(f"{date_str}: {title}" if date_str else title)

        # Time ranges
        if "time [ref=" in line:
            m = re.search(r'time \[ref=\w+\]:\s*(.+)', line)
            if m:
                time_text = m.group(1).strip()
                if ":" in time_text and events:
                    events[-1] += f" ({time_text})"

        # Location
        if "generic [ref=" in line and "  " in line:
            m = re.search(r'generic \[ref=\w+\]:\s*(.+)', line)
            if m:
                text = m.group(1).strip()
                if text and ("RMS" in text or "GYM" in text or "Center" in text or "Library" in text):
                    if events:
                        events[-1] += f" @ {text}"

    return events


def _parse_news(snap):
    """Parse news items from the RMS news section."""
    news = []
    lines = snap.split("\n")
    in_news = False

    for line in lines:
        if 'heading "RMS news"' in line:
            in_news = True
            continue
        if not in_news:
            continue

        # News items as links
        if "link" in line.lower() and "/url:" not in line:
            m = re.search(r'link "([^"]+)"', line)
            if m:
                title = m.group(1).strip()
                if title and len(title) > 15:
                    news.append(title)

        # Or as headings/text
        if "heading" in line.lower() and "level" in line:
            m = re.search(r'heading "([^"]+)"', line)
            if m:
                title = m.group(1).strip()
                if title and len(title) > 15:
                    news.append(title)

    # Deduplicate
    seen = set()
    unique = []
    for n in news:
        if n not in seen:
            seen.add(n)
            unique.append(n)
    return unique[:10]


def _parse_district_news(snap):
    """Parse news/stories from lwsd.org homepage."""
    news = []
    lines = snap.split("\n")

    # District news appears as links with article titles
    for line in lines:
        if "link" in line.lower() and "/url:" not in line:
            m = re.search(r'link "([^"]+)"', line)
            if m:
                title = m.group(1).strip()
                # Filter to actual news articles (skip nav, social, generic)
                if (title and len(title) > 20
                        and "opens in new window" not in title
                        and "News & Stories" not in title
                        and "newsletter" not in title.lower()
                        and not title.startswith("LWSD")
                        and "Skip" not in title):
                    news.append(title)

    # Deduplicate
    seen = set()
    unique = []
    for n in news:
        if n not in seen:
            seen.add(n)
            unique.append(n)
    return unique[:10]
