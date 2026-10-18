"""Read-only scan of the public PTSA site over plain HTTP.

The public pages are server-rendered and need no sign-in, so this never
touches the shared browser: it fetches each page, keeps the text of the page
body (between the site chrome's `PageContainerDiv` and its footer), and
reports a short hash of that text so a later scan can say which pages changed.

Month calendars are read event by event from their hover callouts (title,
date, time, place, ICS id). A multi-day event is drawn once per day in the
grid and is listed once here.
"""
import hashlib
import html as html_mod
import json
import os
import re
import urllib.error
import urllib.request
from datetime import date
from html.parser import HTMLParser

# The PTSA program and board pages Bear Tracks draws on. OSP_SCAN_PAGES in
# .env replaces this list ("Name|/path,Name2|/path2"). The two month
# calendars are always added unless that list names a calendar itself.
DEFAULT_PAGES = [
    ("Home", "/"),
    ("Programs", "/Program"),
    ("Reflections", "/Page/Programs/Reflections"),
    ("Quiz Bowl", "/Page/Programs/QuizBowl"),
    ("Math Club", "/Page/Programs/MathClub"),
    ("Science Team", "/Page/Programs/ScienceTeam"),
    ("Spelling Bee", "/Page/Programs/SpellingBee"),
    ("Drama", "/Page/Programs/Drama"),
    ("Volunteer", "/Page/PTSA/Volunteer"),
    ("Meetings", "/Page/PTSA/Meetings"),
    ("Board of Directors", "/Page/PTSA/BoardofDirectors"),
]
CALENDAR_PATH = "/Event/MonthCalendar"
USER_AGENT = "Mozilla/5.0 (b3t osp scan; read-only)"
BODY_START = 'id="PageContainerDiv"'
BODY_END = 'id="CustomFooterContainerDiv"'
BLOCK_TAGS = {"p", "div", "section", "article", "br", "hr", "h1", "h2", "h3",
              "h4", "h5", "h6", "li", "tr", "table", "ul", "ol", "blockquote"}
SKIP_TAGS = {"script", "style", "noscript"}


def month_calendars(today=None):
    """(name, path) for this month's and next month's calendar."""
    today = today or date.today()
    nxt = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    return [(f"Calendar {d:%B %Y}", f"{CALENDAR_PATH}/{d.year}/{d.month}")
            for d in (today, nxt)]


def scan_pages(env_value="", today=None):
    """The pages to scan: OSP_SCAN_PAGES if set, else the default list."""
    pages = []
    for part in (env_value or "").split(","):
        part = part.strip()
        if "|" in part:
            name, path = part.split("|", 1)
            pages.append((name.strip(), path.strip()))
        elif part:
            pages.append((part, part if part.startswith("/") else f"/{part}"))
    if not pages:
        pages = list(DEFAULT_PAGES)
    if not any(is_calendar(n, p) for n, p in pages):
        pages += month_calendars(today)
    return pages


def is_calendar(name, path):
    return path.startswith(CALENDAR_PATH) or name == "Calendar"


def fetch(url, timeout=30):
    """GET `url`. Returns (final_url, status, html); status 0 on a network error."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode(resp.headers.get_content_charset() or "utf-8",
                                      "replace")
            return resp.geturl(), resp.status, body
    except urllib.error.HTTPError as e:
        return url, e.code, ""
    except (urllib.error.URLError, OSError) as e:
        return url, 0, str(e)


def page_title(html):
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    return re.sub(r"\s+", " ", html_mod.unescape(m.group(1))).strip() if m else ""


def _body(html):
    """The page's own content, without the navigation and footer chrome."""
    start = html.find(BODY_START)
    if start < 0:
        return html
    start = max(0, html.rfind("<", 0, start))
    end = html.find(BODY_END, start)
    if end > 0:
        end = max(start, html.rfind("<", 0, end))
    return html[start:end if end > 0 else len(html)]


class _Text(HTMLParser):
    """Visible text, one block per line, links kept as `[text](href)`."""

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.lines, self.cur = base, [], []
        self.skip = 0
        self.href = None
        self.link_text = []
        self.headers = []

    def _flush(self):
        line = re.sub(r"\s+", " ", "".join(self.cur)).strip()
        if line:
            self.lines.append(line)
        self.cur = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in SKIP_TAGS:
            self.skip += 1
            return
        # OSP draws each box's heading from data-HeaderText, not from markup.
        header = a.get("data-headertext")
        if header:
            self._flush()
            self.lines.append(f"## {header.strip()}")
            self.headers.append(header.strip())
        if tag in BLOCK_TAGS:
            self._flush()
            if tag == "li":
                self.cur.append("- ")
        if tag == "a" and a.get("href"):
            href = a["href"].strip()
            if href.startswith("/"):
                href = self.base + href
            self.href, self.link_text = href, []

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if tag == "a" and self.href is not None:
            text = re.sub(r"\s+", " ", "".join(self.link_text)).strip()
            if text and self.href.startswith("http") and text != self.href:
                self.cur.append(f" ({self.href})")
            self.href = None
        if tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self.skip:
            return
        self.cur.append(data)
        if self.href is not None:
            self.link_text.append(data)

    def close(self):
        super().close()
        self._flush()


def page_text(html, base=""):
    """(lines, box headers) of a page's content area."""
    # A calendar box (the home page has one) is a grid of bare day numbers
    # with each event repeated per day; list its events in its place instead.
    body = re.sub(r'<table class="CalendarTable".*?</table>',
                  lambda m: "".join(f"<p>{html_mod.escape(event_line(ev))}</p>"
                                    for ev in calendar_events(m.group(0))),
                  _body(html), flags=re.S)
    p = _Text(base)
    p.feed(body)
    p.close()
    return p.lines, p.headers


class _Calendar(HTMLParser):
    """Events from a month view: each event's callout holds its details."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events = []
        self.cls = None          # class of the callout div being read
        self.label = None
        self.event = None
        self.buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if "CalloutMiddleDiv" in cls:
            self.event = {"title": "", "date": "", "time": "", "where": "", "ics": ""}
        elif self.event is not None and tag == "div" and cls.startswith("CalloutEvent"):
            self.cls, self.buf = cls, []
        elif self.event is not None and tag == "a":
            m = re.search(r"/Event/ICS/(\d+)", a.get("href") or "")
            if m:
                self.event["ics"] = m.group(1)

    def handle_endtag(self, tag):
        if tag != "div" or self.cls is None or self.event is None:
            return
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        if self.cls == "CalloutEventTitleDiv":
            self.event["title"] = text
        elif self.cls == "CalloutEventLabelDiv":
            self.label = text.rstrip(":").lower()
        elif self.cls == "CalloutEventValueDiv" and self.label:
            if self.label in ("date", "time", "where"):
                self.event[self.label] = text
            if self.label == "ics":
                self.events.append(self.event)
                self.event = None
            self.label = None
        self.cls = None

    def handle_data(self, data):
        if self.cls is not None:
            self.buf.append(data)


def calendar_events(html):
    """Distinct events in a month view, in the order the grid first shows them."""
    p = _Calendar()
    p.feed(html)
    p.close()
    seen, out = set(), []
    for ev in p.events:
        key = ev["ics"] or (ev["title"], ev["date"])
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    return out


def event_line(ev):
    when = ev["date"] + (f" {ev['time']}" if ev["time"] else "")
    where = f" @ {ev['where']}" if ev["where"] else ""
    return f"{when}: {ev['title']}{where}"


def digest(lines):
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:12]


def scan_page(base, name, path, fetcher=fetch):
    """Fetch and summarise one page. Never raises for a bad page."""
    url = base + path
    final, status, html = fetcher(url)
    result = {"page": name, "url": url, "title": "", "hash": "", "lines": [],
              "error": ""}
    # A missing OSP page redirects to /Home/Error404 with a 200.
    if status != 200 or "Error404" in final:
        result["error"] = (f"not found ({final})" if "Error404" in final
                           else f"HTTP {status or 'error'}: {html[:120]}")
        return result
    result["title"] = page_title(html)
    if is_calendar(name, path):
        lines = [event_line(ev) for ev in calendar_events(html)]
        result["lines"] = lines or ["(no events)"]
    else:
        lines, _ = page_text(html, base)
        result["lines"] = lines
    result["hash"] = digest(result["lines"])
    return result


def compare(results, state_path):
    """Mark each result new/changed/unchanged against the last saved hashes,
    then save this scan's hashes. No state file: nothing is marked."""
    if not state_path:
        return
    previous = {}
    if os.path.exists(state_path):
        try:
            with open(state_path) as f:
                previous = json.load(f)
        except (json.JSONDecodeError, OSError):
            previous = {}
    for r in results:
        if r["error"]:
            continue
        old = previous.get(r["url"])
        r["status"] = ("new" if old is None
                       else "unchanged" if old == r["hash"] else "CHANGED")
    current = dict(previous)
    current.update({r["url"]: r["hash"] for r in results if r["hash"]})
    os.makedirs(os.path.dirname(os.path.abspath(state_path)), exist_ok=True)
    with open(state_path, "w") as f:
        json.dump(current, f, indent=2, sort_keys=True)
