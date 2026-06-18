"""ParentSquare feed scanning and submission export.

Usage:
    b3t parentsquare login              # Auto-login using env credentials
    b3t parentsquare scan               # List recent posts with full bodies
    b3t parentsquare scan --json        # Machine-readable output
    b3t parentsquare save --dir PATH    # Write parentsquare-*.md submission files
"""
import json
import os
import re
import sys
import time
from datetime import date

import env
import session
from constants import PARENTSQUARE_BASE, PARENTSQUARE_FEED, PARENTSQUARE_LOGIN

SKIP_TITLES = {"Communicate", "Explore", "Participate", "Events", "Photos"}
BODY_STOP_MARKERS = (
    "Appreciate",
    "Comment",
    "Print",
    "people appreciate this post",
    "User Preferred Notifications",
    "Read More about",
)


def dispatch(args):
    if not PARENTSQUARE_FEED:
        print("ERROR: PARENTSQUARE_SCHOOL_ID must be set in .env", file=sys.stderr)
        return 1
    action = args.action
    if not action:
        print("Usage: b3t parentsquare <login|scan|save>", file=sys.stderr)
        return 2
    if action == "login":
        return cmd_login(args)
    elif action == "scan":
        return cmd_scan(args)
    elif action == "save":
        return cmd_save(args)
    return 2


def ensure_authenticated():
    """Check ParentSquare auth, auto-login using env credentials if needed."""
    session.ensure_running()

    session.navigate(PARENTSQUARE_FEED)
    time.sleep(3)
    url = session.current_url()
    if url and "/signin" not in url and "/login" not in url:
        return True

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
            m = re.search(r"\[ref=(\w+)\]", line)
            if m:
                email_ref = m.group(1)
        if "password" in ll and "textbox" in ll and not pass_ref:
            m = re.search(r"\[ref=(\w+)\]", line)
            if m:
                pass_ref = m.group(1)
        if ("sign in" in ll or "log in" in ll) and "button" in ll and not submit_ref:
            m = re.search(r"\[ref=(\w+)\]", line)
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


def _parse_json_result(stdout):
    for line in stdout.split("\n"):
        line = line.strip()
        if not line or line.startswith("###") or line.startswith("```") or line.startswith("await"):
            continue
        try:
            parsed = json.loads(line)
            if isinstance(parsed, str):
                try:
                    return json.loads(parsed)
                except json.JSONDecodeError:
                    return parsed
            return parsed
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return None


def _scroll_feed():
    """Scroll lazy-loaded feed."""
    session.run("mousewheel", "3000", "0")
    time.sleep(2)
    session.run("mousewheel", "3000", "0")
    time.sleep(2)


def _expand_all_read_more():
    """Expand truncated posts so full bodies are visible."""
    snap = session.snapshot() or ""
    refs = []
    for line in snap.split("\n"):
        if "Read More about" in line and "button" in line.lower():
            m = re.search(r"\[ref=(\w+)\]", line)
            if m:
                refs.append(m.group(1))
    for ref in refs:
        session.run("click", ref, timeout=5)
        time.sleep(0.4)
    if refs:
        time.sleep(1)


def _parse_feed_bodies(snap):
    """Extract post bodies from accessibility snapshot (after Read More expanded)."""
    posts = []
    lines = snap.split("\n")
    skip_titles = SKIP_TITLES
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r'\s*- region "([^"]+)" \[ref=', line)
        if not m:
            i += 1
            continue
        title = m.group(1).strip()
        if title in skip_titles or len(title) < 5:
            i += 1
            continue

        author = ""
        date_str = ""
        body_parts = []
        feed_url = ""
        is_post = False
        i += 1

        while i < len(lines):
            ln = lines[i]
            if re.match(r'\s*- region "', ln) and not ln.strip().startswith("- region"):
                break
            if re.match(r'\s*- region "', ln):
                break
            if re.match(r'^\s{10,}- region "', ln):
                break

            if "Posted by" in ln:
                am = re.search(r'Posted by ([^"]+)"', ln)
                if am:
                    author = am.group(1).strip()
                    is_post = True
            if "/feeds/" in ln and title.replace('"', "") in ln:
                um = re.search(r'/url: (/feeds/\d+)', ln)
                if um:
                    feed_url = PARENTSQUARE_BASE + um.group(1)
            if "days ago" in ln or re.search(r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*, \w+ \d+', ln):
                if "•" in ln:
                    dm = re.search(r'(\d+ days? ago)', ln)
                    if dm:
                        date_str = dm.group(1)
                    else:
                        dm2 = re.search(r'((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*, \w+ \d+[^•]*)', ln)
                        if dm2:
                            date_str = dm2.group(1).strip()

            tm = re.match(r'\s*- text: (.+)$', ln)
            if tm and is_post:
                text = tm.group(1).strip()
                if any(s in text for s in BODY_STOP_MARKERS):
                    i += 1
                    break
                if text and text not in (title, "Read More"):
                    body_parts.append(text)
            pm = re.match(r'\s*- paragraph \[ref=', ln)
            if pm and is_post and i + 1 < len(lines):
                nm = re.match(r'\s*- text: (.+)$', lines[i + 1])
                if nm:
                    text = nm.group(1).strip()
                    if text and not any(s in text for s in BODY_STOP_MARKERS):
                        body_parts.append(text)

            if is_post and "Appreciate" in ln and "button" in ln.lower():
                break
            i += 1

        if is_post and author:
            posts.append({
                "title": title,
                "author": author,
                "date": date_str,
                "feed_url": feed_url,
                "body": "\n".join(body_parts).strip(),
                "links": [],
            })
        if i < len(lines) and re.match(r'\s*- region "', lines[i]):
            continue
    return posts


def _fetch_posts(expand=True):
    """Return list of post dicts with title, author, date, feed_url, body."""
    if expand:
        _expand_all_read_more()
    snap = session.snapshot()
    if not snap:
        return []
    return _parse_feed_bodies(snap)


def _days_ago_from_date(date_str):
    """Parse '6 days ago' or 'Jun 8' style strings; return days ago or None."""
    if not date_str:
        return None
    m = re.search(r"(\d+)\s+days?\s+ago", date_str)
    if m:
        return int(m.group(1))
    return None


def _filter_since(posts, since_days):
    if not since_days:
        return posts
    filtered = []
    for post in posts:
        days = _days_ago_from_date(post.get("date", ""))
        if days is None or days <= since_days:
            filtered.append(post)
    return filtered


def _slugify(title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:72] or "post"


def _write_submission(path, post):
    today = date.today().isoformat()
    title = post.get("title", "Untitled")
    author = post.get("author", "")
    date_str = post.get("date", "")
    feed_url = post.get("feed_url", "")
    body = post.get("body", "").strip()
    links = post.get("links") or []

    lines = [
        f"# {title}",
        "",
        "**Source:** ParentSquare",
    ]
    if feed_url:
        lines.append(f"**URL:** {feed_url}")
    if author:
        lines.append(f"**Author:** {author}")
    if date_str:
        lines.append(f"**Posted:** {date_str}")
    lines.append(f"**Gathered:** {today}")
    lines.append("")
    lines.append("## Body")
    lines.append("")
    lines.append(body or "_(empty)_")
    if links:
        lines.append("")
        lines.append("## Links")
        lines.append("")
        seen = set()
        for link in links:
            href = link.get("href", "")
            text = link.get("text", href)
            if href in seen:
                continue
            seen.add(href)
            lines.append(f"- [{text}]({href})")

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def cmd_scan(args):
    """Scan recent feed posts with full bodies."""
    if not ensure_authenticated():
        return 1

    url = session.current_url()
    if not url or "feeds" not in url:
        session.navigate(PARENTSQUARE_FEED)
        time.sleep(3)

    _scroll_feed()
    posts = _fetch_posts(expand=True)
    posts = _filter_since(posts, getattr(args, "since", None))

    print(f"{len(posts)} posts found", file=sys.stderr)

    if getattr(args, "json", False):
        print(json.dumps(posts, indent=2))
        return 0

    for i, post in enumerate(posts, 1):
        meta = post.get("date", "")
        author = post.get("author", "")
        title = post.get("title", "")
        header = f"{meta} | {author}" if meta and author else meta or author
        print(f"\n{'=' * 60}")
        print(f"{i}. [{header}] {title}")
        if post.get("feed_url"):
            print(f"URL: {post['feed_url']}")
        print("-" * 60)
        body = post.get("body", "").strip()
        if body:
            preview = body if len(body) <= 1200 else body[:1200] + "\n…"
            print(preview)
        else:
            print("(no body text captured)")
    return 0


def cmd_save(args):
    """Save feed posts as submission markdown files."""
    if not ensure_authenticated():
        return 1

    out_dir = os.path.abspath(args.dir)
    since_days = getattr(args, "since", None)

    url = session.current_url()
    if not url or "feeds" not in url:
        session.navigate(PARENTSQUARE_FEED)
        time.sleep(3)

    _scroll_feed()
    posts = _fetch_posts(expand=True)
    posts = _filter_since(posts, since_days)

    os.makedirs(out_dir, exist_ok=True)
    written = 0
    for post in posts:
        slug = _slugify(post.get("title", "post"))
        path = os.path.join(out_dir, f"parentsquare-{slug}.md")
        _write_submission(path, post)
        written += 1
        print(path)

    print(f"Wrote {written} files to {out_dir}", file=sys.stderr)
    session.save_state()
    return 0
