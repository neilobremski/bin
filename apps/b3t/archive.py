"""Turn a sent edition into the body of its archive page on rmsptsa.org.

The email and the archive page are not the same document. The email opens with
the PTSA logo and the edition date, and ends with the unsubscribe line that
Givebacks fills in at send time. The page carries the date in its own title, and
an unsubscribe link on a public web page is nonsense, so those three rows come
off. Everything between them is the edition exactly as it was sent.

Nothing here is rewritten or reflowed. The archive pages already on the site are
the email's own Unlayer rows, images still pointing at S3, and the point of this
module is that a reader in March sees what subscribers saw in September.

Every rule below is checked rather than assumed. A silently wrong archive page
is worse than no archive page: it is published, public, and nobody rereads an
edition from six months ago closely enough to notice the masthead is missing.
"""
from __future__ import annotations

import html
import re
from datetime import date

ROW_OPEN = '<div class="u-row-container"'

# "September 20, 2026", the date heading Unlayer renders under the logo.
DATE_HEADING = re.compile(
    r'^(January|February|March|April|May|June|July|August|September|October'
    r'|November|December)\s+\d{1,2},\s+\d{4}$')

# Same shape, captured, for reading a heading back into y/m/d.
_LONG_DATE = re.compile(
    r'^(January|February|March|April|May|June|July|August|September|October'
    r'|November|December)\s+(\d{1,2}),\s+(\d{4})$')

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


class ArchiveError(Exception):
    """The edition does not look the way the archive page needs it to."""


def _text(html):
    """Visible text of a fragment, whitespace collapsed."""
    out = re.sub(r'<[^>]+>', ' ', html)
    out = (out.replace('&nbsp;', ' ').replace('&amp;', '&')
              .replace('&lt;', '<').replace('&gt;', '>').replace('&#39;', "'")
              .replace('&quot;', '"'))
    return re.sub(r'\s+', ' ', out).strip()


def _row_end(html, start):
    """Index just past the `</div>` that closes the row opening at `start`.

    Unlayer nests divs several deep inside every row, so the first `</div>` is
    never the right one. Count openings and closings instead.
    """
    depth = 0
    for m in re.finditer(r'<div\b|</div\s*>', html[start:]):
        depth += 1 if m.group(0).startswith('<div') else -1
        if depth == 0:
            return start + m.end()
    raise ArchiveError('a row never closes; the sent HTML is truncated')


def rows(raw_html):
    """The edition's top-level rows, in order, as HTML strings."""
    found = []
    for m in re.finditer(re.escape(ROW_OPEN), raw_html):
        if found and m.start() < found[-1][1]:
            continue                      # nested inside the row before it
        found.append((m.start(), _row_end(raw_html, m.start())))
    return [raw_html[a:b] for a, b in found]


def _is_image_only(row):
    return '<img' in row and not _text(row)


# Marks that identify Givebacks' unsubscribe footer even when they live in
# an attribute rather than in visible text, e.g.
# `<a href="$UnsubscribeLink">Unsubscribe</a>`, where `_text()` strips the
# tag and the href along with it and leaves only the innocuous word
# "Unsubscribe". Checked against the raw row HTML, not the stripped text.
UNSUBSCRIBE_MARKERS = ('$UnsubscribeLink', 'Copyright Givebacks')


def _is_unsubscribe(row):
    if any(marker in row for marker in UNSUBSCRIBE_MARKERS):
        return True
    t = _text(row)
    return any(marker in t for marker in UNSUBSCRIBE_MARKERS)


def date_heading(row):
    """The date this row announces, or None if it is not a date heading."""
    t = _text(row)
    return t if DATE_HEADING.match(t) else None


def long_date(date):
    """`2026-09-20` as `September 20, 2026`, the way the site writes it."""
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', (date or '').strip())
    if not m:
        raise ArchiveError('edition date must be YYYY-MM-DD, got %r' % date)
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not 1 <= mo <= 12:
        raise ArchiveError('edition date has no such month: %r' % date)
    return '%s %d, %d' % (MONTHS[mo - 1], d, y)


def short_date(text):
    """`September 20, 2026` (as the site writes it) back to `2026-09-20`."""
    m = _LONG_DATE.match((text or '').strip())
    if not m:
        raise ArchiveError('not a date heading: %r' % text)
    month, day, year = m.group(1), int(m.group(2)), int(m.group(3))
    return '%04d-%02d-%02d' % (year, MONTHS.index(month) + 1, day)


def heading_date(raw_html):
    """The edition's own date, read from its date-heading row.

    Skips the masthead logo row if the first row is one, the same as
    `build()`, but otherwise reads without validating the rest of the
    edition's shape: this is for finding out what `--edition` should be
    before anything else about the HTML is checked.
    """
    body = rows(raw_html)
    if not body:
        raise ArchiveError('found no rows; that is not an edition')
    if _is_image_only(body[0]):
        body = body[1:]
    if not body:
        raise ArchiveError('no rows after the masthead; that is not an edition')
    heading = date_heading(body[0])
    if not heading:
        raise ArchiveError('the second row is not a date heading (it reads %r)'
                           % _text(body[0])[:60])
    return short_date(heading)


def _date_obj(text):
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', (text or '').strip())
    if not m:
        raise ArchiveError('date must be YYYY-MM-DD, got %r' % text)
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def resolve_edition_date(explicit, heading, send_date=None):
    """Pick the edition date for `gb archive`, and say why.

    An explicit `--edition` is used unchanged, whatever the heading or send
    date say (`build()` still checks it against the heading and refuses a
    mismatch there, unchanged). Omitted, the heading is trusted as the
    edition date, but only when it is close enough before the send to
    plausibly be the same edition: Givebacks' send date is often a day or
    more after the date the newsletter itself is dated (a Sunday-night send
    for a Monday edition, say), so the window is 0-3 days. No send date at
    all means the item is still a draft, accepted outright since there is
    nothing yet to cross-check against.

    Returns `(date, reason)`.
    """
    if explicit:
        return explicit, 'explicit --edition'
    if not send_date:
        return heading, 'no send date on this item (draft); using the heading date as-is'
    delta = (_date_obj(send_date) - _date_obj(heading)).days
    if 0 <= delta <= 3:
        return heading, 'heading is %d day(s) before the %s send' % (delta, send_date)
    raise ArchiveError(
        'the heading says %s but Givebacks sent it %s (%d days apart, outside '
        'the 0-3 day window this trusts). Pass --edition explicitly.'
        % (heading, send_date, delta))


def page_slug(date):
    """The page's address. `-english` because the site also archives Spanish."""
    long_date(date)                        # validates the shape
    return '%s-english' % date


def school_year(date):
    """The Aug-Jul school year an edition date falls in, e.g. `2026-2027`.

    An edition dated in Aug-Dec belongs to the year starting that August; one
    dated Jan-Jul belongs to the year that started the August before.
    """
    long_date(date)                        # validates the shape
    y, mo = int(date[:4]), int(date[5:7])
    return '%d-%d' % (y, y + 1) if mo >= 8 else '%d-%d' % (y - 1, y)


def page_title(date, subject):
    """The page title, matching every archive page already on the site."""
    subject = (subject or '').strip()
    if not subject:
        raise ArchiveError('no subject: the archive title needs one')
    return '%s [English]: %s' % (long_date(date), subject)


def build(raw_html, date, subject=None):
    """The archive page body for an edition. Returns (html, dropped_rows).

    `raw_html` is what Givebacks sends, taken from the message record rather
    than the design, because the design is not what landed in anyone's inbox.
    """
    if not (raw_html or '').strip():
        raise ArchiveError('the edition has no sent HTML yet; send or '
                           'regenerate it first')
    body = rows(raw_html)
    if len(body) < 5:
        raise ArchiveError('found %d rows; that is not an edition' % len(body))

    dropped = []

    if not _is_image_only(body[0]):
        raise ArchiveError(
            'the first row is not the masthead logo (it reads %r). Refusing to '
            'guess which rows to drop.' % _text(body[0])[:60])
    dropped.append('masthead logo')
    body = body[1:]

    heading = date_heading(body[0])
    if not heading:
        raise ArchiveError(
            'the second row is not the date heading (it reads %r)'
            % _text(body[0])[:60])
    if heading != long_date(date):
        raise ArchiveError(
            'the edition says %r but --edition says %r. One of them is wrong.'
            % (heading, long_date(date)))
    dropped.append('date heading (%s)' % heading)
    body = body[1:]

    if not _is_unsubscribe(body[-1]):
        raise ArchiveError(
            'the last row is not the unsubscribe footer (it reads %r)'
            % _text(body[-1])[:60])
    dropped.append('unsubscribe footer')
    body = body[:-1]

    # An unsubscribe link anywhere else would still be live on a public page.
    for row in body:
        if _is_unsubscribe(row):
            raise ArchiveError('an unsubscribe row survived in the middle of '
                               'the edition; not publishing that')

    if subject is not None:
        page_title(date, subject)          # fail here, not after publishing

    return '\n'.join(body) + '\n', dropped


def highlights(raw_html, limit=12):
    """The At a Glance lines, for the entry on the archive listing page."""
    for row in rows(raw_html):
        text = _text(row)
        if 'At a glance' not in text:
            continue
        items = []
        for li in re.findall(r'<li\b[^>]*>(.*?)</li>', row, re.S | re.I):
            line = _text(li)
            if line:
                items.append(line)
        if items:
            return items[:limit]
    return []


# --------------------------------------------------------- archive listing
#
# The listing page (`/Page/BearTracks/Archive`) is a second, separate page:
# one `<h5>Bear Tracks Archive for YYYY-YYYY</h5>` per school year, newest
# year first, each followed by a `<ul>` of entries, newest edition first:
#
#   <h5>Bear Tracks Archive for 2026-2027</h5>
#   <ul>
#   <li><a href="https://rmsptsa.org/Page/BearTracks/2026-09-20-english">...</a>
#   <ul>
#   <li>...</li>
#   </ul>
#   </li>
#   </ul>
#   <p>&nbsp;</p>
#
# A `<ul>` here nests another `<ul>` (each entry's highlights), so finding
# where one ends takes the same depth-counting `_row_end` uses for a row's
# `</div>`, not a plain search for the next closing tag.

_SECTION = re.compile(r'<h5>Bear Tracks Archive for (\d{4}-\d{4})</h5>')


def _closing_tag_span(text, open_at, tag):
    """(start, end) of the `</tag>` that closes the opening tag at `open_at`."""
    pattern = re.compile(r'<%s\b[^>]*>|</%s\s*>' % (tag, tag), re.I)
    depth = 0
    for m in pattern.finditer(text, open_at):
        if m.group(0).startswith('</'):
            depth -= 1
            if depth == 0:
                return m.start(), m.end()
        else:
            depth += 1
    raise ArchiveError('a <%s> never closes in the archive listing' % tag)


def _section_spans(listing_html):
    """Every school-year section's boundary, in document order (newest first).

    Each entry is `(year, header_start, section_end)`, where `section_end` is
    the start of the next section's `<h5>` header, or the end of the document
    for the last one. This only locates headers; it says nothing about what a
    section's body looks like, because the real listing has a legacy
    2024-2025 section that is a `<table>` rather than a `<ul>`, and every
    section but the one being touched must be left alone rather than parsed.
    """
    matches = list(_SECTION.finditer(listing_html))
    out = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(listing_html)
        out.append((m.group(1), m.start(), end))
    return out


def _section_list(listing_html, year, header_start, section_end):
    """The `(entries_start, entries_end)` span of one section's `<ul>...</ul>`.

    Searched only within `header_start:section_end`, that section's own
    boundary, so a differently-shaped section elsewhere in the document (the
    legacy table) is never inspected, let alone required to be list-shaped.
    """
    ul_start = listing_html.find('<ul', header_start, section_end)
    if ul_start == -1:
        raise ArchiveError('the %s section has no <ul> of entries' % year)
    entries_start = listing_html.find('>', ul_start) + 1
    close_start, close_end = _closing_tag_span(listing_html, ul_start, 'ul')
    return entries_start, close_start


def _entries(section_html):
    """Top-level `<li>...</li>` entries of a section, each with its trailing
    newline (if any) kept, so joining the list back together reproduces the
    section exactly."""
    tag = re.compile(r'<li\b[^>]*>|</li\s*>', re.I)
    depth, start, out = 0, None, []
    for m in tag.finditer(section_html):
        if not m.group(0).startswith('</'):
            if depth == 0:
                start = m.start()
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                end = m.end()
                if section_html[end:end + 1] == '\n':
                    end += 1
                out.append(section_html[start:end])
    return out


_ENTRY_DATE = re.compile(r'Page/BearTracks/(\d{4}-\d{2}-\d{2})-english')


def listing_insert(listing_html, date, title, highlights):
    """Add one edition's entry to the archive listing. Returns the new HTML.

    Refuses outright, computing nothing, if the edition's slug is already
    linked anywhere in the listing. Inserts in date order within the edition's
    school-year section (normally at the top, since editions usually arrive
    newest first); creates the section, immediately before the newest
    existing one, if this is the first edition of its school year.
    """
    slug = page_slug(date)                 # validates the date's shape
    year = school_year(date)

    href_marker = 'Page/BearTracks/%s"' % slug
    if href_marker in listing_html:
        raise ArchiveError(
            '%s is already linked in the archive listing; not adding it twice'
            % slug)

    entry = ('<li><a href="https://rmsptsa.org/Page/BearTracks/%s">%s</a>\n'
              '<ul>\n%s</ul>\n</li>\n') % (
        slug, html.escape(title, quote=False),
        ''.join('<li>%s</li>\n' % html.escape(h, quote=False) for h in highlights))

    sections = _section_spans(listing_html)
    for section_year, header_start, section_end in sections:
        if section_year != year:
            continue
        entries_start, entries_end = _section_list(
            listing_html, year, header_start, section_end)
        insert_at = entries_start
        for existing in _entries(listing_html[entries_start:entries_end]):
            m = _ENTRY_DATE.search(existing)
            if not m:
                raise ArchiveError(
                    'an existing entry in the %s section has no dated link; '
                    'refusing to guess where the new one goes' % year)
            if m.group(1) < date:
                break
            insert_at += len(existing)
        return listing_html[:insert_at] + entry + listing_html[insert_at:]

    # No section for this school year yet. New sections are always the
    # newest one seen so far in practice (editions arrive in order), so it
    # goes immediately before whatever section is currently first, or at
    # the very top of the page if there are no sections at all yet.
    new_section = ('<h5>Bear Tracks Archive for %s</h5>\n<ul>\n%s</ul>\n'
                   '<p>&nbsp;</p>\n') % (year, entry)
    insert_at = sections[0][1] if sections else 0
    return listing_html[:insert_at] + new_section + listing_html[insert_at:]
