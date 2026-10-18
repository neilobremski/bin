"""`osp scan` covers the program pages, not just the home page.

Until 2026-10-01 the scan read only `/` (OSP_SCAN_PAGES was never set), so a
stale Reflections page or a new board member went unseen. It now fetches the
program, volunteer, meetings and board pages plus this and next month's
calendar over plain HTTP (public pages, no browser), and gives each a title
and a hash of its text so a later scan can say what changed.

No network: the fetcher is a fake. The HTML copies the real OSP page shapes.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ospscan

BASE = "https://ptsa.example.org"


def page(body, title="Reflections"):
    return f"""<html><head><title>
\t{title}
</title><style>h3 {{ color: red }}</style></head><body>
<div id="NavigationBarDiv"><a href="/Page/PTSA/Volunteer">Volunteer nav</a></div>
<div id="PageContainerDiv" class="HeaderStyleSolid">
<div class="FullBox" data-HeaderText="{title}"><div class="NarrowMarginsDiv">
{body}
</div></div></div>
<div id="CustomFooterContainerDiv"><p>Footer text</p></div>
</body></html>"""


def callout(title, when, ics, where="", time=""):
    extra = ""
    if time:
        extra += (f'<div class="CalloutEventLabelDiv">Time:</div>'
                  f'<div class="CalloutEventValueDiv">{time}</div>')
    if where:
        extra += (f'<div class="CalloutEventLabelDiv">Where:</div>'
                  f'<div class="CalloutEventValueDiv">{where}</div>')
    return f"""<div class='AllDayEvent'>{title}
<div class="CalloutDiv HoverDiv"><div class="CalloutTopDiv"></div>
<div class="CalloutMiddleDiv">
<div class="CalloutEventTitleDiv">{title}</div><hr />
<div class="CalloutEventLabelDiv">Date:</div><div class="CalloutEventValueDiv">{when}</div>
{extra}<hr /><div class="CalloutEventDescriptionDiv">Details <a href="https://x.example">here</a></div>
<div class="CalloutEventLabelDiv">ics:</div>
<div class="CalloutEventValueDiv"> <a href="/Event/ICS/{ics}"> to add to calendar</a></div>
</div><div class="CalloutBottomDiv"></div></div></div>"""


CALENDAR = page(f"""<table class="CalendarTable">
<tr class="DayOfWeekRow"><th>Sunday</th><th>Monday</th></tr>
<tr class="DateRow"><td><div>Sep 27</div></td><td><div>28</div></td></tr>
<tr class="EventsRow"><td>{callout("Club Fair", "Mon 9/28 - Fri 10/2", 1, "Commons")}</td>
<td>{callout("Club Fair", "Mon 9/28 - Fri 10/2", 1, "Commons")}</td></tr>
<tr class="EventsRow"><td>{callout("Board Meeting", "Tuesday, October 20", 2, "", "6:30p - 7:30p")}</td></tr>
</table>""", title="October 2026")


def fake_fetch(pages):
    def fetch(url):
        path = url[len(BASE):]
        if path in pages:
            return url, 200, pages[path]
        return f"{BASE}/Home/Error404?aspxerrorpath={path}", 200, page("not found")
    return fetch


def test_page_text_is_the_content_area_with_links_and_headers():
    html = page('<p>The theme is <strong>&ldquo;Culture&rdquo;</strong>.</p>'
                '<ul><li>Entries due <a href="/Packet/Reflections">here</a></li></ul>'
                '<script>var x = "never shown";</script>')
    lines, headers = ospscan.page_text(html, BASE)
    assert lines == ["## Reflections", "The theme is “Culture”.",
                     f"- Entries due here ({BASE}/Packet/Reflections)"]
    assert headers == ["Reflections"]
    assert ospscan.page_title(html) == "Reflections"


def test_calendar_lists_each_event_once():
    events = ospscan.calendar_events(CALENDAR)
    assert [ospscan.event_line(e) for e in events] == [
        "Mon 9/28 - Fri 10/2: Club Fair @ Commons",
        "Tuesday, October 20 6:30p - 7:30p: Board Meeting",
    ]


def test_scan_page_reports_title_hash_and_a_missing_page():
    fetch = fake_fetch({"/Page/Programs/Reflections": page("<p>Due Oct 30</p>"),
                        "/Event/MonthCalendar/2026/10": CALENDAR})
    ok = ospscan.scan_page(BASE, "Reflections", "/Page/Programs/Reflections", fetch)
    assert ok["title"] == "Reflections" and not ok["error"]
    assert len(ok["hash"]) == 12 and ok["lines"][-1] == "Due Oct 30"

    cal = ospscan.scan_page(BASE, "Calendar October 2026", "/Event/MonthCalendar/2026/10", fetch)
    assert cal["lines"] == ["Mon 9/28 - Fri 10/2: Club Fair @ Commons",
                            "Tuesday, October 20 6:30p - 7:30p: Board Meeting"]

    gone = ospscan.scan_page(BASE, "Calendar", "/Page/Calendar", fetch)
    assert "not found" in gone["error"] and not gone["hash"]


def test_hash_changes_only_when_the_text_does():
    a = ospscan.scan_page(BASE, "R", "/r", fake_fetch({"/r": page("<p>One</p>")}))
    b = ospscan.scan_page(BASE, "R", "/r", fake_fetch({"/r": page("<p>One</p>\n\n")}))
    c = ospscan.scan_page(BASE, "R", "/r", fake_fetch({"/r": page("<p>Two</p>")}))
    assert a["hash"] == b["hash"] != c["hash"]


def test_state_file_marks_new_changed_unchanged(tmp_path):
    state = str(tmp_path / "osp-state.json")
    pages = {"/a": page("<p>A</p>"), "/b": page("<p>B</p>")}
    first = [ospscan.scan_page(BASE, n, p, fake_fetch(pages)) for n, p in (("A", "/a"), ("B", "/b"))]
    ospscan.compare(first, state)
    assert [r["status"] for r in first] == ["new", "new"]

    pages["/b"] = page("<p>B, updated</p>")
    second = [ospscan.scan_page(BASE, n, p, fake_fetch(pages)) for n, p in (("A", "/a"), ("B", "/b"))]
    ospscan.compare(second, state)
    assert [r["status"] for r in second] == ["unchanged", "CHANGED"]
    assert json.load(open(state))[f"{BASE}/b"] == second[1]["hash"]


def test_default_pages_cover_programs_board_and_two_calendars():
    pages = dict((p, n) for n, p in ospscan.scan_pages("", today=date(2026, 12, 15)))
    for path in ("/Page/Programs/Reflections", "/Page/Programs/QuizBowl",
                 "/Page/Programs/MathClub", "/Page/Programs/ScienceTeam",
                 "/Page/Programs/SpellingBee", "/Page/Programs/Drama",
                 "/Page/PTSA/Volunteer", "/Page/PTSA/Meetings",
                 "/Page/PTSA/BoardofDirectors"):
        assert path in pages
    # December rolls over into January of the next year.
    assert pages["/Event/MonthCalendar/2026/12"] == "Calendar December 2026"
    assert pages["/Event/MonthCalendar/2027/1"] == "Calendar January 2027"


def test_env_list_replaces_the_defaults():
    pages = ospscan.scan_pages("Home|/,Calendar|/Event/MonthCalendar")
    assert pages == [("Home", "/"), ("Calendar", "/Event/MonthCalendar")]
    pages = ospscan.scan_pages("Home|/", today=date(2026, 10, 1))
    assert [p for _, p in pages] == ["/", "/Event/MonthCalendar/2026/10",
                                     "/Event/MonthCalendar/2026/11"]
