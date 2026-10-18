"""ParentSquare feed scanning and submission export.

Usage:
    b3t parentsquare login              # Auto-login using env credentials
    b3t parentsquare scan               # List recent posts with full bodies
    b3t parentsquare scan --json        # Machine-readable output
    b3t parentsquare save --dir PATH    # Write parentsquare-*.md submission files
    b3t ps scan --since 14              # Walk feed pages back 14 days
"""
import json
import os
import re
import sys
import time
from datetime import date
from urllib.parse import parse_qs, urlparse

import env
import session
from constants import PARENTSQUARE_BASE, PARENTSQUARE_FEED, PARENTSQUARE_LOGIN

SKIP_TITLES = {"Communicate", "Explore", "Participate", "Events", "Photos"}
# Text that belongs to the post's chrome, never to its body.
BODY_SKIP = (
    "This post can be viewed by users outside of ParentSquare",
    "Read More",
)
# Roles whose line carries no body text worth keeping.
BODY_SKIP_ROLES = {"button", "img", "status", "/url", "separator"}
# Roles that run inline with their neighbours rather than starting a line.
INLINE_ROLES = {"text", "strong", "emphasis", "link", "code", "mark", "subscript",
                "superscript", "insertion", "deletion"}
# The feed is paginated (`?page=N`), ten posts a page. Walking back to a
# `--since` cutoff stops here and says so, rather than paging forever.
MAX_PAGES = 5
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
     "nov", "dec"], 1)}

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
        session.run("click", pass_ref)
        session.run("press", "Enter")

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
    """Scroll the feed page so anything rendered on scroll is in the DOM."""
    session.run("mousewheel", "0", "3000")
    time.sleep(2)
    session.run("mousewheel", "0", "3000")
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


class _Node:
    __slots__ = ("indent", "role", "name", "value")

    def __init__(self, indent, role, name, value):
        self.indent, self.role, self.name, self.value = indent, role, name, value


_NODE = re.compile(
    r'^(?P<role>/?[\w-]+)'
    r'(?: "(?P<name>(?:[^"\\]|\\.)*)")?'
    r'(?P<attrs>(?: \[[^\]]*\])*)'
    r'(?::(?: (?P<value>.*))?)?$')


def _unquote_yaml(text):
    """Undo the YAML quoting the snapshot puts on a line or value.

    A line holding a colon is single-quoted whole (`- 'region "RMS: Fire
    Alarm" [ref=e1]':`), with `''` for a literal quote; a value may be
    double-quoted JSON-style (`- strong [ref=e2]: "Schedule:"`).
    """
    if text.startswith("'"):
        out, i = [], 1
        while i < len(text):
            if text[i] == "'":
                if text[i + 1:i + 2] == "'":
                    out.append("'")
                    i += 2
                    continue
                break
            out.append(text[i])
            i += 1
        return "".join(out) + text[i + 1:]
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return text[1:-1]
    return text


def _node(line):
    """One snapshot line as a node, or None for anything else."""
    m = re.match(r"^(\s*)- (.*)$", line)
    if not m:
        return None
    nm = _NODE.match(_unquote_yaml(m.group(2).rstrip()))
    if not nm:
        return None
    name = nm.group("name")
    if name is not None:
        try:
            name = json.loads(f'"{name}"')
        except (json.JSONDecodeError, ValueError):
            pass
    value = nm.group("value")
    if value is not None:
        value = _unquote_yaml(value.strip())
    return _Node(len(m.group(1)), nm.group("role"), name, value)


def _unwrap_link(href):
    """The real target behind an Outlook safelinks wrapper, else `href`."""
    if "safelinks.protection.outlook.com" in href:
        q = parse_qs(urlparse(href).query)
        if q.get("url"):
            return q["url"][0]
    return href


def days_ago(relative, absolute, today=None):
    """Age in days of a post stamped `• 2 days ago • Tuesday, Sep 29 at ... •`.

    Either half may be missing. The relative half is read first ("4 hours
    ago" is today, "a day ago" is one); the absolute one ("Tuesday, Sep 29",
    "Sep 29, 2025") is the fallback, its year inferred as the latest one that
    is not in the future. None when neither half can be read.
    """
    today = today or date.today()
    rel = (relative or "").lower()
    if re.search(r"\b(just now|seconds?|minutes?|hours?|an hour|a minute)\b", rel):
        return 0
    if re.search(r"\b(a day ago|yesterday)\b", rel):
        return 1
    m = re.search(r"(\d+)\s+days?\s+ago", rel)
    if m:
        return int(m.group(1))
    for text in (absolute, relative):
        m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.? "
                      r"(\d{1,2})(?:,? (\d{4}))?", (text or "").lower())
        if not m:
            continue
        month, day = MONTHS[m.group(1)], int(m.group(2))
        try:
            if m.group(3):
                when = date(int(m.group(3)), month, day)
            else:
                when = date(today.year, month, day)
                if when > today:
                    when = date(today.year - 1, month, day)
        except ValueError:
            continue
        return (today - when).days
    return None


def _join_pieces(pieces):
    """Body pieces to lines: inline runs at one depth join with spaces."""
    lines, buf, buf_indent = [], [], None
    for indent, inline, text in pieces:
        if inline and buf and indent == buf_indent:
            buf.append(text)
            continue
        if buf:
            lines.append(" ".join(buf))
        buf, buf_indent = ([text], indent) if inline else ([], None)
        if not inline:
            lines.append(text)
    if buf:
        lines.append(" ".join(buf))
    return [re.sub(r" ([.,;:!?)])", r"\1", ln).strip() for ln in lines if ln.strip()]


def _parse_post(nodes, start, end, heading):
    """Read one post region: header (link, title, byline, stamp), then body."""
    title = nodes[start].name
    feed_url = ""
    link_indent = nodes[heading].indent
    for k in range(heading - 1, start, -1):
        n = nodes[k]
        if n and n.role == "/url" and re.match(r"/feeds/\d+$", n.value or ""):
            feed_url = PARENTSQUARE_BASE + n.value
        if n and n.role == "link":
            link_indent = n.indent
            break

    # The header block (avatar, title link, byline list) sits at the title
    # link's depth or deeper; the first shallower node starts the body.
    body_start = end
    for k in range(heading + 1, end):
        n = nodes[k]
        if n and n.indent < link_indent:
            body_start = k
            break

    author = relative = absolute = ""
    for k in range(heading + 1, body_start):
        n = nodes[k]
        if not n:
            continue
        if n.role == "link" and (n.name or "").startswith("Posted by "):
            author = n.name[len("Posted by "):].strip()
        elif n.role == "listitem" and n.value and not author:
            # District posts carry a plain name ("Communications"), no link.
            author = n.value.strip()
        elif n.role == "text" and (n.value or "").startswith("•"):
            parts = [p.strip() for p in n.value.split("•") if p.strip()]
            relative = parts[0] if parts else ""
            absolute = parts[1] if len(parts) > 1 else ""

    pieces, links = [], []
    for k in range(body_start, end):
        n = nodes[k]
        if not n:
            continue
        if n.role == "button" and n.name == "Appreciate":
            break
        if n.role == "status":
            break
        if n.role == "/url":
            if links and not links[-1]["href"]:
                links[-1]["href"] = _unwrap_link(n.value or "")
            continue
        if n.role in BODY_SKIP_ROLES:
            continue
        if n.role == "link":
            text = n.name or n.value or ""
            links.append({"text": text, "href": ""})
        elif n.role == "heading":
            text = n.name or n.value or ""
        else:
            text = n.value or ""
        text = text.strip()
        if not text or any(text.startswith(s) for s in BODY_SKIP):
            continue
        if n.role == "listitem":
            text = f"- {text}"
        pieces.append((n.indent, n.role in INLINE_ROLES, text))

    return {
        "title": title,
        "author": author,
        "date": absolute or relative,
        "days_ago": days_ago(relative, absolute),
        "feed_url": feed_url,
        "body": "\n".join(_join_pieces(pieces)).strip(),
        "links": [l for l in links if l["href"]],
    }


def _parse_feed_bodies(snap):
    """Every post on a feed page, from its accessibility snapshot.

    A post is a region holding a level-2 heading of its own name (the feed's
    outer "Posts" region holds none). Its extent is its indentation subtree,
    so a post whose title has a colon (the snapshot single-quotes that line)
    or an escaped quote is found like any other; both shapes were silently
    dropped before, which is how page 1 on 2026-10-01 parsed 6 of 10 posts.
    """
    nodes = [_node(l) for l in snap.split("\n")]
    posts = []
    for i, n in enumerate(nodes):
        if not n or n.role != "region" or not n.name:
            continue
        title = n.name.strip()
        if title in SKIP_TITLES:
            continue
        end = len(nodes)
        for j in range(i + 1, len(nodes)):
            if nodes[j] and nodes[j].indent <= n.indent:
                end = j
                break
        heading = next((k for k in range(i + 1, end)
                        if nodes[k] and nodes[k].role == "heading"
                        and (nodes[k].name or "").strip() == title), None)
        if heading is None:
            continue
        posts.append(_parse_post(nodes, i, end, heading))
    return posts


def _has_next_page(snap, page):
    return bool(re.search(r"feeds\?page=%d\b" % (page + 1), snap or ""))


def walk_pages(fetch_page, since_days=None, max_pages=MAX_PAGES):
    """Collect posts page by page. Returns (posts, partial).

    Without `since_days` only page 1 is read, as before. With it, pages are
    read until one ends with a post older than the cutoff, or the feed runs
    out. Hitting `max_pages` first, or a page that cannot be read (`fetch_page`
    returns None, twice), is reported as partial: a scan that quietly stopped
    short reads as "nothing else was posted".
    """
    posts, seen = [], set()
    page = 1
    while True:
        snap = fetch_page(page)
        if snap is None:
            snap = fetch_page(page)
        if snap is None:
            print(f"PARTIAL: could not read page {page} of the feed; posts on "
                  "it and any later pages are missing.", file=sys.stderr)
            return posts, True
        found = _parse_feed_bodies(snap)
        print(f"Page {page}: {len(found)} posts", file=sys.stderr)
        for post in found:
            key = post.get("feed_url") or (post.get("title"), post.get("date"))
            if key not in seen:
                seen.add(key)
                posts.append(post)
        if not since_days or not found:
            return posts, False
        last = found[-1].get("days_ago")
        if last is not None and last > since_days:
            return posts, False
        if not _has_next_page(snap, page):
            return posts, False
        if page >= max_pages:
            print(f"PARTIAL: stopped at the {max_pages}-page cap and page "
                  f"{page} still holds posts from the last {since_days} days. "
                  "Raise --max-pages to read further back.", file=sys.stderr)
            return posts, True
        page += 1


def _fetch_page(page):
    """Load feed page `page` in the browser, expand it, and snapshot it.
    None if the snapshot fails, which is not the same as an empty page."""
    url = PARENTSQUARE_FEED if page == 1 else f"{PARENTSQUARE_FEED}?page={page}"
    # `"feeds" in url` also matches ?page=3 from an earlier walk, which would
    # make page 1 read some other page.
    if (session.current_url() or "").rstrip("/") != url:
        session.navigate(url)
        time.sleep(3)
    _scroll_feed()
    _expand_all_read_more()
    return session.snapshot()


def _days_ago_from_date(date_str):
    """Days ago for a stored post date string, or None."""
    return days_ago(date_str, date_str)


def _filter_since(posts, since_days):
    if not since_days:
        return posts
    filtered = []
    for post in posts:
        days = post.get("days_ago")
        if days is None:
            days = _days_ago_from_date(post.get("date", ""))
        if days is None or days <= since_days:
            filtered.append(post)
    return filtered


def _collect(args):
    """(posts, partial) for the scan/save arguments."""
    since_days = getattr(args, "since", None)
    max_pages = getattr(args, "max_pages", None) or MAX_PAGES
    posts, partial = walk_pages(_fetch_page, since_days, max_pages)
    return _filter_since(posts, since_days), partial


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

    posts, partial = _collect(args)
    # A partial read still prints what it has, then exits 1 so a gather
    # cannot mistake it for the whole feed.
    rc = 1 if partial else 0

    print(f"{len(posts)} posts found" + (" (PARTIAL, see above)" if partial else ""),
          file=sys.stderr)

    if getattr(args, "json", False):
        print(json.dumps(posts, indent=2))
        return rc

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
    return rc


def cmd_save(args):
    """Save feed posts as submission markdown files."""
    if not ensure_authenticated():
        return 1

    out_dir = os.path.abspath(args.dir)
    posts, partial = _collect(args)

    os.makedirs(out_dir, exist_ok=True)
    written = 0
    for post in posts:
        slug = _slugify(post.get("title", "post"))
        path = os.path.join(out_dir, f"parentsquare-{slug}.md")
        _write_submission(path, post)
        written += 1
        print(path)

    print(f"Wrote {written} files to {out_dir}"
          + (" (PARTIAL, see above)" if partial else ""), file=sys.stderr)
    session.save_state()
    return 1 if partial else 0
