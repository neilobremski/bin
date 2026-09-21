"""Archive-page rules.

An archive page is the sent edition minus three rows: the masthead logo, the
date heading (the page's title carries the date instead), and the unsubscribe
footer that Givebacks fills in at send time. Those three are identified by what
they contain, never by counting from the ends, because a future edition will
have a different number of rows and the wrong three would come off in silence.

Everything here is checked against the shape of the pages already on
rmsptsa.org, `/Page/BearTracks/YYYY-MM-DD-english`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import archive

LOGO_ROW = (
    '<div class="u-row-container" style="padding:0px"><div class="u-row">'
    '<div class="u-col"><table><tr><td>'
    '<img src="https://s3/1730662413034-bear-tracks-newsletter-logo.png"'
    ' alt="Bear Tracks Newsletter"/>'
    '</td></tr></table></div></div></div>')

DATE_ROW = (
    '<div class="u-row-container"><div class="u-row"><div class="u-col">'
    '<table><tr><td><h1>September&nbsp;20, 2026</h1></td></tr></table>'
    '</div></div></div>')

GLANCE_ROW = (
    '<div class="u-row-container"><div class="u-row"><div class="u-col">'
    '<table><tr><td><p><strong>At a glance</strong></p><ul>'
    '<li>\U0001f4da Meet the Teachers</li>'
    '<li>\U0001f52c Sep 22: Science Club Registration Closes</li>'
    '<li></li></ul></td></tr></table></div></div></div>')

ARTICLE_ROW = (
    '<div class="u-row-container"><div class="u-row"><div class="u-col">'
    '<table><tr><td><h1>Choir Boosters</h1>'
    '<p>They meet in the library on <a href="https://rmsptsa.org/">Monday</a>.</p>'
    '</td></tr></table></div></div></div>')

CREDIT_ROW = (
    '<div class="u-row-container"><div class="u-row"><div class="u-col">'
    '<table><tr><td><p>Bear Tracks is produced by RMS PTSA.</p>'
    '</td></tr></table></div></div></div>')

FOOTER_ROW = (
    '<div class="u-row-container"><div class="u-row"><div class="u-col">'
    '<table><tr><td><p>$UnsubscribeLink<br/>Copyright Givebacks'
    ' All rights reserved.</p></td></tr></table></div></div></div>')

PREAMBLE = ('<meta charset="utf-8"/><style type="text/css">'
            '.u-row { width: 600px; } @media only screen { p { margin: 0; } }'
            '</style><table class="nl-container"><tbody><tr><td>')

EDITION = PREAMBLE + "".join([
    LOGO_ROW, DATE_ROW, GLANCE_ROW, ARTICLE_ROW, CREDIT_ROW, FOOTER_ROW,
]) + "</td></tr></tbody></table>"


@pytest.fixture
def page():
    html, _ = archive.build(EDITION, "2026-09-20", "Bear Tracks - Meet the Teachers")
    return html


def test_the_three_rows_that_do_not_belong_on_a_web_page_come_off(page):
    assert "bear-tracks-newsletter-logo.png" not in page
    assert "September" not in page
    assert "$UnsubscribeLink" not in page
    assert "Copyright Givebacks" not in page


def test_the_edition_itself_is_untouched(page):
    assert page.count('<div class="u-row-container"') == 3
    assert "At a glance" in page
    assert "Choir Boosters" in page
    assert "produced by RMS PTSA" in page
    assert 'href="https://rmsptsa.org/"' in page
    for row in (GLANCE_ROW, ARTICLE_ROW, CREDIT_ROW):
        assert row in page


def test_the_stylesheet_and_the_wrapper_table_stay_behind(page):
    """The site supplies the page frame; TinyMCE strips a <style> anyway."""
    assert "<style" not in page
    assert "nl-container" not in page
    assert page.lstrip().startswith('<div class="u-row-container"')


def test_it_says_what_it_dropped():
    _, dropped = archive.build(EDITION, "2026-09-20", "Bear Tracks - Meet the Teachers")
    assert dropped == ["masthead logo", "date heading (September 20, 2026)",
                       "unsubscribe footer"]


def test_the_wrong_edition_date_is_caught_not_published():
    """`--edition 2026-09-06` against the Sep 20 HTML would archive it twice,
    the second time over the top of the first."""
    with pytest.raises(archive.ArchiveError) as e:
        archive.build(EDITION, "2026-09-06", "Bear Tracks - Meet the Teachers")
    assert "September 20, 2026" in str(e.value)
    assert "September 6, 2026" in str(e.value)


def test_an_edition_that_does_not_start_with_the_logo_is_refused():
    no_logo = EDITION.replace(LOGO_ROW, "")
    with pytest.raises(archive.ArchiveError) as e:
        archive.build(no_logo, "2026-09-20", "s")
    assert "masthead" in str(e.value)


def test_an_edition_that_does_not_end_with_the_footer_is_refused():
    no_footer = EDITION.replace(FOOTER_ROW, "")
    with pytest.raises(archive.ArchiveError) as e:
        archive.build(no_footer, "2026-09-20", "s")
    assert "unsubscribe" in str(e.value)


def test_an_unsubscribe_link_further_up_is_refused():
    """It would be live, and public, and would work."""
    stray = EDITION.replace(ARTICLE_ROW, ARTICLE_ROW.replace(
        "They meet", "$UnsubscribeLink They meet"))
    with pytest.raises(archive.ArchiveError) as e:
        archive.build(stray, "2026-09-20", "s")
    assert "middle" in str(e.value)


def test_an_unsubscribe_link_hidden_in_an_href_is_also_refused():
    """`_text()` strips tags and attributes, so a real anchor whose visible
    text is just "Unsubscribe" and whose target lives in the `href` must be
    caught from the raw HTML, not the stripped text."""
    stray = EDITION.replace(ARTICLE_ROW, ARTICLE_ROW.replace(
        "They meet",
        '<a href="$UnsubscribeLink">Unsubscribe</a> They meet'))
    with pytest.raises(archive.ArchiveError) as e:
        archive.build(stray, "2026-09-20", "s")
    assert "middle" in str(e.value)


def test_a_footer_holding_only_an_unsubscribe_anchor_is_still_detected():
    """The footer marker itself may be an anchor rather than plain text."""
    anchor_footer = (
        '<div class="u-row-container"><div class="u-row"><div class="u-col">'
        '<table><tr><td><p><a href="$UnsubscribeLink">Unsubscribe</a><br/>'
        'Copyright Givebacks All rights reserved.</p>'
        '</td></tr></table></div></div></div>')
    html = EDITION.replace(FOOTER_ROW, anchor_footer)
    page, dropped = archive.build(html, "2026-09-20", "s")
    assert dropped[-1] == "unsubscribe footer"
    assert "$UnsubscribeLink" not in page
    assert 'href="$UnsubscribeLink"' not in page


def test_an_unsent_edition_has_nothing_to_archive():
    with pytest.raises(archive.ArchiveError) as e:
        archive.build("", "2026-09-20", "s")
    assert "no sent HTML" in str(e.value)


def test_rows_are_the_top_level_ones_only():
    """`u-row-container` also appears nested inside a row on some editions."""
    nested = ARTICLE_ROW.replace(
        "<h1>Choir", '<div class="u-row-container">inner</div><h1>Choir')
    assert len(archive.rows(PREAMBLE + LOGO_ROW + nested)) == 2


def test_the_title_matches_the_pages_already_on_the_site():
    assert archive.page_title("2026-09-20", "Bear Tracks - Meet the Teachers") == \
        "September 20, 2026 [English]: Bear Tracks - Meet the Teachers"
    assert archive.page_title("2025-10-12", "Bear Tracks - Yearbook Photos Requested") == \
        "October 12, 2025 [English]: Bear Tracks - Yearbook Photos Requested"


def test_the_page_address_matches_the_archive_listing():
    assert archive.page_slug("2026-09-20") == "2026-09-20-english"


@pytest.mark.parametrize("bad", ["", "20260920", "Sep 20 2026", "2026-13-01"])
def test_a_date_the_site_cannot_use_is_refused(bad):
    with pytest.raises(archive.ArchiveError):
        archive.page_slug(bad)


def test_a_title_needs_a_subject():
    with pytest.raises(archive.ArchiveError):
        archive.page_title("2026-09-20", "  ")


def test_the_listing_entry_comes_from_at_a_glance():
    assert archive.highlights(EDITION) == [
        "\U0001f4da Meet the Teachers",
        "\U0001f52c Sep 22: Science Club Registration Closes"]


# ------------------------------------------------------------ school_year

def test_school_year_straddles_august():
    assert archive.school_year("2026-08-24") == "2026-2027"
    assert archive.school_year("2026-08-01") == "2026-2027"
    assert archive.school_year("2026-07-31") == "2025-2026"
    assert archive.school_year("2026-05-31") == "2025-2026"


# ------------------------------------------------------------ heading_date

def test_heading_date_reads_the_editions_own_date():
    assert archive.heading_date(EDITION) == "2026-09-20"


def test_heading_date_refused_without_a_heading():
    no_date = EDITION.replace(DATE_ROW, "")
    with pytest.raises(archive.ArchiveError) as e:
        archive.heading_date(no_date)
    assert "date heading" in str(e.value)


# ------------------------------------------------------- resolve_edition_date

def test_resolve_edition_date_omitted_within_window_uses_the_heading():
    date, reason = archive.resolve_edition_date(None, "2026-05-31", "2026-06-01")
    assert date == "2026-05-31"
    assert "2026-06-01" in reason


def test_resolve_edition_date_omitted_too_far_off_is_refused():
    with pytest.raises(archive.ArchiveError) as e:
        archive.resolve_edition_date(None, "2026-05-25", "2026-06-01")
    assert "2026-05-25" in str(e.value)
    assert "2026-06-01" in str(e.value)


def test_resolve_edition_date_omitted_heading_after_the_send_is_refused():
    """A send date can only ever be on or after its own edition's heading."""
    with pytest.raises(archive.ArchiveError):
        archive.resolve_edition_date(None, "2026-06-05", "2026-06-01")


def test_resolve_edition_date_omitted_with_no_send_date_is_a_draft():
    date, reason = archive.resolve_edition_date(None, "2026-09-20", None)
    assert date == "2026-09-20"
    assert "draft" in reason


def test_resolve_edition_date_explicit_is_used_unchanged():
    """An explicit --edition is trusted even against a wildly different
    heading or send date; `build()` is what checks it against the heading."""
    date, reason = archive.resolve_edition_date("2026-09-20", "2026-01-01", "2020-01-01")
    assert date == "2026-09-20"
    assert "explicit" in reason


# ---------------------------------------------------------- listing_insert

_ENTRY_MAY3 = (
    '<li><a href="https://rmsptsa.org/Page/BearTracks/2026-05-03-english">'
    'May 3, 2026 [English]: Bear Tracks - The Home Stretch</a>\n'
    '<ul>\n<li>Staff Appreciation Week</li>\n</ul>\n</li>\n')

_ENTRY_APR5 = (
    '<li><a href="https://rmsptsa.org/Page/BearTracks/2026-04-05-english">'
    'April 5, 2026 [English]: Bear Tracks - Science in Spring</a>\n'
    '<ul>\n<li>Science Olympiad</li>\n</ul>\n</li>\n')

_ENTRY_2025JUN = (
    '<li><a href="https://rmsptsa.org/Page/BearTracks/2025-06-01-english">'
    'June 1, 2025 [English]: Bear Tracks - The Last Word</a>\n'
    '<ul>\n<li>Yearbook</li>\n</ul>\n</li>\n')

LISTING = (
    '<h5>Bear Tracks Archive for 2025-2026</h5>\n<ul>\n'
    + _ENTRY_MAY3 + _ENTRY_APR5 +
    '</ul>\n<p>&nbsp;</p>\n'
    '<h5>Bear Tracks Archive for 2024-2025</h5>\n<ul>\n'
    + _ENTRY_2025JUN +
    '</ul>\n<p>&nbsp;</p>\n'
)


def _section(listing_html, year):
    after = listing_html.split('Bear Tracks Archive for %s' % year)[1]
    return after.split('<h5>')[0]


def test_listing_creates_a_new_section_for_a_new_school_year():
    out = archive.listing_insert(
        LISTING, "2026-08-24",
        "August 24, 2026 [English]: Bear Tracks - Welcome Back, Grizzlies!",
        ["First day of school Aug 31"])
    assert "2026-2027" in out
    assert out.index("2026-2027") < out.index("2025-2026")
    assert "2026-08-24-english" in out
    # existing sections and entries survive untouched
    assert _ENTRY_MAY3 in out
    assert _ENTRY_2025JUN in out


def test_listing_creates_the_new_section_right_before_the_newest_one():
    out = archive.listing_insert(
        LISTING, "2026-08-24", "Bear Tracks - Welcome Back, Grizzlies!", [])
    assert out.index("2026-2027") < out.index("2025-2026") < out.index("2024-2025")


def test_listing_inserts_at_the_top_of_an_existing_section():
    out = archive.listing_insert(
        LISTING, "2026-05-17",
        "May 17, 2026 [English]: Bear Tracks - The Final Stretch",
        ["Field Day"])
    section = _section(out, "2025-2026")
    assert section.index("2026-05-17-english") < section.index("2026-05-03-english")


def test_listing_inserts_in_the_middle_by_date():
    out = archive.listing_insert(
        LISTING, "2026-04-19",
        "April 19, 2026 [English]: Bear Tracks - Welcome Back",
        ["Spring Break"])
    section = _section(out, "2025-2026")
    assert (section.index("2026-05-03-english") < section.index("2026-04-19-english")
            < section.index("2026-04-05-english"))


def test_listing_refuses_a_duplicate_slug():
    with pytest.raises(archive.ArchiveError) as e:
        archive.listing_insert(
            LISTING, "2026-05-03",
            "May 3, 2026 [English]: Bear Tracks - The Home Stretch (again)", [])
    assert "already linked" in str(e.value)
    assert "2026-05-03-english" in str(e.value)


def test_listing_entry_html_escapes_title_and_highlights():
    out = archive.listing_insert(
        LISTING, "2026-08-24", "Bear Tracks - R&D Night",
        ["<b>Big</b> News & More"])
    assert "R&amp;D Night" in out
    assert "&lt;b&gt;Big&lt;/b&gt; News &amp; More" in out
    assert "<b>Big</b>" not in out


# ---------------------------------------- listing_insert / legacy table fixture

_REAL_LISTING = (Path(__file__).resolve().parent / "fixtures" / "listing_real.html").read_text()


def test_listing_insert_survives_a_legacy_table_section():
    """The real listing's oldest section (2024-2025) is a `<table>`, not a
    `<ul>` -- `_section_spans`/`_section_list` must never require it to be
    list-shaped just because some other section is being edited."""
    out = archive.listing_insert(
        _REAL_LISTING, "2026-10-04",
        "October 4, 2026 [English]: Bear Tracks - Fall Forward",
        ["Fall Fest"])
    section = _section(out, "2026-2027")
    assert section.index("2026-10-04-english") < section.index("2026-09-20-english")
    # the untouched sections, including the legacy table, are byte-for-byte
    # unchanged
    assert _section(out, "2025-2026") == _section(_REAL_LISTING, "2025-2026")
    legacy_before = _REAL_LISTING[_REAL_LISTING.index("<h5>Bear Tracks Archive for 2024-2025"):]
    legacy_after = out[out.index("<h5>Bear Tracks Archive for 2024-2025"):]
    assert legacy_before == legacy_after
    assert "<table" in legacy_after


def test_listing_insert_new_year_does_not_require_the_legacy_table_to_be_a_list():
    """Creating a brand-new 2027-2028 section must not touch, or even parse,
    any older section -- including the 2024-2025 table."""
    out = archive.listing_insert(
        _REAL_LISTING, "2027-08-30",
        "August 30, 2027 [English]: Bear Tracks - A New Year",
        ["Welcome back"])
    assert out.index("2027-2028") < out.index("2026-2027") < out.index("2025-2026")
    assert "2027-08-30-english" in _section(out, "2027-2028")
    legacy_before = _REAL_LISTING[_REAL_LISTING.index("<h5>Bear Tracks Archive for 2024-2025"):]
    legacy_after = out[out.index("<h5>Bear Tracks Archive for 2024-2025"):]
    assert legacy_before == legacy_after


def test_listing_2025_2026_vs_2026_2027_boundary():
    """2026-05-31 is the tail of 2025-2026; 2026-08-24 opens 2026-2027,
    even though both are "2026" dates."""
    in_2025_2026 = archive.listing_insert(LISTING, "2026-05-31", "s", [])
    assert "2025-2026" in in_2025_2026
    assert _section(in_2025_2026, "2025-2026").index("2026-05-31-english") >= 0
    assert "2026-2027" not in in_2025_2026

    in_2026_2027 = archive.listing_insert(LISTING, "2026-08-24", "s", [])
    assert in_2026_2027.index("2026-2027") < in_2026_2027.index("2025-2026")
