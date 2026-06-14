"""ParentSquare feed scanning.

Usage:
    b3t parentsquare login       # Auto-login using env credentials
    b3t parentsquare scan        # List recent feed posts (titles, dates, authors)
"""
import os
import re
import sys
import time

import env
import session
from constants import PARENTSQUARE_BASE, PARENTSQUARE_FEED, PARENTSQUARE_LOGIN


def dispatch(args):
    if not PARENTSQUARE_FEED:
        print("ERROR: PARENTSQUARE_SCHOOL_ID must be set in .env", file=sys.stderr)
        return 1
    action = args.action
    if not action:
        print("Usage: b3t parentsquare <login|scan>", file=sys.stderr)
        return 2
    if action == "login":
        return cmd_login(args)
    elif action == "scan":
        return cmd_scan(args)
    return 2


def ensure_authenticated():
    """Check ParentSquare auth, auto-login using env credentials if needed."""
    session.ensure_running()

    session.navigate(PARENTSQUARE_FEED)
    time.sleep(3)
    url = session.current_url()
    if url and "/signin" not in url and "/login" not in url:
        return True

    # Auto-login
    user = os.environ.get("PARENTSQUARE_USER")
    passw = os.environ.get("PARENTSQUARE_PASS")
    if not user or not passw:
        print("ERROR: Set PARENTSQUARE_USER and PARENTSQUARE_PASS in .env", file=sys.stderr)
        return False

    print("Logging in to ParentSquare...", file=sys.stderr)
    session.navigate(PARENTSQUARE_LOGIN)
    time.sleep(3)

    snap = session.snapshot()
    if not snap:
        return False

    email_ref = pass_ref = submit_ref = None
    for line in snap.split("\n"):
        ll = line.lower()
        if "email" in ll and "textbox" in ll and not email_ref:
            m = re.search(r'\[ref=(\w+)\]', line)
            if m:
                email_ref = m.group(1)
        if "password" in ll and "textbox" in ll and not pass_ref:
            m = re.search(r'\[ref=(\w+)\]', line)
            if m:
                pass_ref = m.group(1)
        if ("sign in" in ll or "log in" in ll) and "button" in ll and not submit_ref:
            m = re.search(r'\[ref=(\w+)\]', line)
            if m:
                submit_ref = m.group(1)

    if not email_ref or not pass_ref:
        print("ERROR: Cannot find login fields.", file=sys.stderr)
        return False

    session.run("fill", email_ref, user)
    session.run("fill", pass_ref, passw)

    if submit_ref:
        session.run("click", submit_ref)
    else:
        session.run("press", pass_ref, "Enter")

    time.sleep(4)
    url = session.current_url()
    if url and "/signin" in url:
        print("ERROR: Login failed.", file=sys.stderr)
        return False

    session.save_state()
    print("ParentSquare login successful.", file=sys.stderr)
    return True


def cmd_login(args):
    if ensure_authenticated():
        print("ParentSquare authenticated.", file=sys.stderr)
        return 0
    return 1


def cmd_scan(args):
    """Scan recent feed posts — titles, dates, authors.

    Deterministic: login → navigate → scroll twice → parse → output.
    Only captures top-level posts (has "Posted by" metadata), not nested
    sub-headings within posts like the Connections newsletter.
    """
    if not ensure_authenticated():
        return 1

    # Navigate to feed
    url = session.current_url()
    if not url or "feeds" not in url:
        session.navigate(PARENTSQUARE_FEED)
        time.sleep(3)

    # Scroll to load more posts (feed is lazy-loaded)
    # mousewheel: first arg is vertical (despite label saying dx)
    session.run("mousewheel", "3000", "0")
    time.sleep(2)
    session.run("mousewheel", "3000", "0")
    time.sleep(2)

    snap = session.snapshot()
    if not snap:
        print("ERROR: Cannot get feed snapshot.", file=sys.stderr)
        return 1

    posts = _parse_feed(snap)

    print(f"{len(posts)} posts found", file=sys.stderr)
    for i, post in enumerate(posts, 1):
        date = post.get("date", "")
        author = post.get("author", "")
        title = post.get("title", "")
        meta = f"{date} | {author}" if date and author else date or author
        print(f"  {i}. [{meta}] {title}")

    return 0


def _parse_feed(snap):
    """Parse top-level feed posts from snapshot.

    Structure in accessibility tree:
      region "Posts"
        ...
          heading "Post Title" [level=2]
          ...
            link "Posted by Author Name"
          ...
            text: • N days ago • DayOfWeek, Mon DD at HH:MM PM •
        ...

    Key insight: real top-level posts have "Posted by" within ~8 lines after
    their heading. Nested sub-headings (inside article bodies like the
    Connections newsletter) do NOT have "Posted by" nearby.
    """
    posts = []
    lines = snap.split("\n")
    in_posts = False
    skip_titles = {"Communicate", "Explore", "Participate", "Events", "Photos"}

    i = 0
    while i < len(lines):
        line = lines[i]

        if 'region "Posts"' in line:
            in_posts = True
            i += 1
            continue

        if not in_posts:
            i += 1
            continue

        # Look for h2 headings
        if 'heading "' in line and "[level=2]" in line:
            m = re.search(r'heading "([^"]+)"', line)
            if not m:
                i += 1
                continue
            title = m.group(1).strip()
            if title in skip_titles or len(title) < 5:
                i += 1
                continue

            # Look ahead for "Posted by" (proves this is a top-level post)
            author = ""
            date_str = ""
            is_real_post = False

            for j in range(i + 1, min(i + 12, len(lines))):
                # Author
                if "Posted by" in lines[j]:
                    am = re.search(r'Posted by ([^"]+)"', lines[j])
                    if am:
                        author = am.group(1).strip()
                    is_real_post = True

                # Date: text line with bullet-separated metadata
                # Format: • N days ago • DayOfWeek, Mon DD at HH:MM PM •
                if "• " in lines[j] and "days ago" in lines[j]:
                    dm = re.search(r'(\d+ days?) ago', lines[j])
                    if dm:
                        date_str = dm.group(1) + " ago"
                elif "• " in lines[j] and "at " in lines[j]:
                    # "• Wednesday, Jun 10 at 12:31 PM •"
                    dm = re.search(
                        r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*, (\w+ \d+)',
                        lines[j]
                    )
                    if dm:
                        date_str = dm.group(1)

            if is_real_post:
                posts.append({
                    "title": title,
                    "author": author,
                    "date": date_str,
                })

        i += 1

    return posts
