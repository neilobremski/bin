"""ParentSquare feed parsing and paging.

On 2026-10-01 `ps save --since` read 6 of the 10 posts on page 1 and never
looked at page 2. Two snapshot shapes were dropped:

* a title holding a colon: the snapshot single-quotes the whole line
  (`- 'region "RMS: Fire Alarm" [ref=e1]':`);
* a title holding a double quote: it is backslash-escaped inside the name;

and the district's Connections post has no "Posted by" link at all. The feed
is paginated (`?page=N`), so `--since` must walk pages until it passes the
cutoff, and say so when a page cap stops it first.

The snapshot below is synthetic but copies the real shapes line for line.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import pytest

os.environ.setdefault("PARENTSQUARE_SCHOOL_ID", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import parentsquare as ps


def post(title, feed_id, stamp, byline=None, body=(), quoted=False):
    """Lines for one post, in the snapshot's own shape."""
    region = f'region "{title}" [ref=e{feed_id}]'
    heading = f'heading "{title}" [level=2] [ref=e{feed_id}h]'
    if quoted:
        region = "'" + region.replace("'", "''") + "':"
        heading = "'" + heading.replace("'", "''") + "'"
    else:
        region += ":"
    out = [
        "            - listitem:",
        f"              - {region}",
        "                - generic:",
        "                  - generic:",
        '                    - \'img "User Profile Icon: AB" [ref=e1]\': AB',
        "                    - link [ref=e2] [cursor=pointer]:",
        f"                      - /url: /feeds/{feed_id}",
        f"                      - {heading}",
        "                    - list [ref=e3]:",
    ]
    if byline:
        out += [
            "                      - listitem [ref=e4]:",
            f'                        - link "Posted by {byline}" [ref=e5] [cursor=pointer]:',
            "                          - /url: /schools/1/users/2",
            f"                          - text: {byline}",
        ]
    else:
        out += ["                      - listitem [ref=e4]: Communications"]
    out += [
        "                      - listitem [ref=e6]:",
        f"                        - text: • {stamp} •",
        "                        - generic [ref=e7]: .",
        "                        - text: Example Middle School",
    ]
    out += ["                  " + b for b in body]
    out += [
        "                  - list [ref=e8]:",
        "                    - listitem [ref=e9]:",
        '                      - button "Appreciate" [ref=e10] [cursor=pointer]',
        "                    - listitem [ref=e11]:",
        '                      - link "Print" [ref=e12] [cursor=pointer]:',
        f"                        - /url: /feeds/{feed_id}?media=print",
        "                  - generic [ref=e13]: User Preferred Notifications • Parents",
        "                  - status [ref=e14]: 4 people appreciate this post",
    ]
    return out


def feed(*posts, page=1, last_page=3):
    lines = ['        - region "Posts" [ref=e0]:', "          - list [ref=e00]:"]
    for p in posts:
        lines += p
    lines += ['          - navigation "Pagination":', "            - list:"]
    if page < last_page:
        lines += [
            '              - link "Go to next page" [ref=e99] [cursor=pointer]:',
            f"                - /url: /schools/1/feeds?page={page + 1}",
            "                - text: Next →",
        ]
    return "\n".join(lines)


SAFE = ("https://nam02.safelinks.protection.outlook.com/?url=https%3A%2F%2F"
        "www.signupgenius.com%2Fgo%2Fexample&data=05%7C02")

PAGE_ONE = feed(
    post("Unit 1 Test", 101, "4 hours ago • Thursday, Oct 1 at 2:55 PM", "Pat Teacher",
         body=["- generic [ref=e20]:",
               "  - text: The study guide for the test on",
               "  - strong [ref=e21]: October 9th",
               "  - text: . It is on Canvas."]),
    post("RMS: Fire Alarm After School", 102, "a day ago • Wednesday, Sep 30 at 1:36 PM",
         "Sam Principal", quoted=True,
         body=["- paragraph [ref=e22]: The alarm was cleaning dust."]),
    post('Friday - Band \\"shadow\\" night', 103, "2 days ago • Tuesday, Sep 29 at 12:54 PM",
         "Lee Band", body=['- strong [ref=e23]: "Schedule:"',
                           "- list [ref=e24]:",
                           "  - listitem [ref=e25]: 5:45 PM - uniforms"]),
    post("Connections - September 25, 2026: Town Hall", 104,
         "6 days ago • Friday, Sep 25 at 8:15 AM", None, quoted=True,
         body=["- generic [ref=e26]: This post can be viewed by users outside of ParentSquare",
               "- article [ref=e27]:",
               '  - link "Read the accessible version" [ref=e28] [cursor=pointer]:',
               "    - /url: https://example.org/connections"]),
    post("Volunteers Needed", 105, "8 days ago • Wednesday, Sep 23 at 2:01 PM",
         "Kim Volunteer",
         body=["- generic [ref=e29]:",
               '  - text: "Sign up:"',
               '  - link "https://www.signupgenius.com/go/example" [ref=e30] [cursor=pointer]:',
               f"    - /url: {SAFE}"]),
)


def test_every_post_on_the_page_is_read():
    posts = ps._parse_feed_bodies(PAGE_ONE)
    assert [p["title"] for p in posts] == [
        "Unit 1 Test",
        "RMS: Fire Alarm After School",
        'Friday - Band "shadow" night',
        "Connections - September 25, 2026: Town Hall",
        "Volunteers Needed",
    ]


def test_header_fields():
    posts = {p["title"]: p for p in ps._parse_feed_bodies(PAGE_ONE)}
    p = posts["Unit 1 Test"]
    assert p["author"] == "Pat Teacher"
    assert p["feed_url"] == "https://www.parentsquare.com/feeds/101"
    assert p["date"] == "Thursday, Oct 1 at 2:55 PM"
    assert p["days_ago"] == 0
    # The district post has a plain name where the byline link would be.
    assert posts["Connections - September 25, 2026: Town Hall"]["author"] == "Communications"


def test_body_keeps_inline_bold_in_its_sentence_and_drops_chrome():
    posts = {p["title"]: p for p in ps._parse_feed_bodies(PAGE_ONE)}
    assert posts["Unit 1 Test"]["body"] == \
        "The study guide for the test on October 9th. It is on Canvas."
    assert posts["RMS: Fire Alarm After School"]["body"] == "The alarm was cleaning dust."
    band = posts['Friday - Band "shadow" night']["body"]
    assert "Schedule:" in band and "- 5:45 PM - uniforms" in band
    for p in posts.values():
        assert "appreciate" not in p["body"].lower()
        assert "Notifications" not in p["body"]
        assert "viewed by users outside" not in p["body"]


def test_links_are_kept_and_safelinks_unwrapped():
    posts = {p["title"]: p for p in ps._parse_feed_bodies(PAGE_ONE)}
    assert posts["Volunteers Needed"]["links"] == [
        {"text": "https://www.signupgenius.com/go/example",
         "href": "https://www.signupgenius.com/go/example"}]
    assert posts["Connections - September 25, 2026: Town Hall"]["links"][0]["href"] == \
        "https://example.org/connections"


@pytest.mark.parametrize("relative,absolute,expected", [
    ("4 hours ago", "", 0),
    ("an hour ago", "", 0),
    ("a day ago", "", 1),
    ("12 days ago", "", 12),
    ("", "Tuesday, Sep 29 at 12:54 PM", 2),
    ("", "Dec 30", 275),           # last year, not three months ahead
    ("", "Sep 3, 2025", 393),
    ("", "", None),
])
def test_days_ago(relative, absolute, expected):
    assert ps.days_ago(relative, absolute, today=date(2026, 10, 1)) == expected


def _pages(spec, last_page=None):
    """spec: {page: [(feed_id, days)]} -> fetch_page fake recording calls."""
    last_page = last_page or max(spec)
    calls = []

    def fetch(page):
        calls.append(page)
        posts = [post(f"Post {fid}", fid, f"{d} days ago", "A B") for fid, d in spec.get(page, [])]
        return feed(*posts, page=page, last_page=last_page)
    return fetch, calls


def test_since_walks_pages_until_past_the_cutoff():
    fetch, calls = _pages({1: [(1, 1), (2, 4)], 2: [(3, 6), (4, 9)],
                           3: [(5, 12), (6, 20)], 4: [(7, 30)]})
    posts, partial = ps.walk_pages(fetch, since_days=10)
    assert calls == [1, 2, 3]                  # page 3 ends past 10 days
    assert not partial
    assert [p["feed_url"][-1] for p in ps._filter_since(posts, 10)] == list("1234")


def test_no_since_reads_page_one_only():
    fetch, calls = _pages({1: [(1, 1)], 2: [(2, 3)]})
    ps.walk_pages(fetch)
    assert calls == [1]


def test_hitting_the_page_cap_is_reported_partial(capsys):
    fetch, calls = _pages({n: [(n, 1)] for n in range(1, 10)})
    posts, partial = ps.walk_pages(fetch, since_days=30, max_pages=3)
    assert calls == [1, 2, 3] and partial
    assert "PARTIAL" in capsys.readouterr().err


def test_last_page_stops_the_walk():
    fetch, calls = _pages({1: [(1, 1)], 2: [(2, 2)]}, last_page=2)
    _, partial = ps.walk_pages(fetch, since_days=30)
    assert calls == [1, 2] and not partial


def test_a_post_seen_on_two_pages_is_kept_once():
    fetch, _ = _pages({1: [(1, 1), (2, 2)], 2: [(2, 2), (3, 40)]})
    posts, _ = ps.walk_pages(fetch, since_days=30)
    assert [p["feed_url"][-1] for p in posts] == ["1", "2", "3"]


def _second_page_fails(times):
    """Page 1 advertises page 2; page 2's snapshot fails `times` times."""
    page1 = feed(post("Recent post", 101, "1 day ago", "A B"), page=1, last_page=2)
    page2 = feed(post("Second post", 102, "2 days ago", "A B"), page=2, last_page=2)
    calls, left = [], [times]

    def fetch(page):
        calls.append(page)
        if page == 2 and left[0] > 0:
            left[0] -= 1
            return None
        return page1 if page == 1 else page2
    return fetch, calls


def test_an_unreadable_page_is_partial_not_the_end_of_the_feed(capsys):
    fetch, calls = _second_page_fails(times=2)
    posts, partial = ps.walk_pages(fetch, since_days=14)
    assert partial and len(posts) == 1 and calls == [1, 2, 2]
    assert "PARTIAL" in capsys.readouterr().err


def test_a_page_that_fails_once_is_fetched_again():
    fetch, calls = _second_page_fails(times=1)
    posts, partial = ps.walk_pages(fetch, since_days=14)
    assert not partial and len(posts) == 2 and calls == [1, 2, 2]


def test_scan_exits_nonzero_on_a_partial_walk(monkeypatch, capsys):
    import json
    from types import SimpleNamespace
    fetch, _ = _second_page_fails(times=2)
    monkeypatch.setattr(ps, "ensure_authenticated", lambda: True)
    monkeypatch.setattr(ps, "_fetch_page", fetch)
    rc = ps.cmd_scan(SimpleNamespace(since=14, max_pages=5, json=True))
    out = capsys.readouterr()
    assert rc == 1 and len(json.loads(out.out)) == 1 and "PARTIAL" in out.err


def test_scan_of_a_healthy_feed_exits_zero(monkeypatch, capsys):
    import json
    from types import SimpleNamespace
    fetch, _ = _second_page_fails(times=0)
    monkeypatch.setattr(ps, "ensure_authenticated", lambda: True)
    monkeypatch.setattr(ps, "_fetch_page", fetch)
    rc = ps.cmd_scan(SimpleNamespace(since=14, max_pages=5, json=True))
    out = capsys.readouterr()
    assert rc == 0 and len(json.loads(out.out)) == 2 and "PARTIAL" not in out.err
