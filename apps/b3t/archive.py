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

import re

ROW_OPEN = '<div class="u-row-container"'

# "September 20, 2026", the date heading Unlayer renders under the logo.
DATE_HEADING = re.compile(
    r'^(January|February|March|April|May|June|July|August|September|October'
    r'|November|December)\s+\d{1,2},\s+\d{4}$')

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


def _is_unsubscribe(row):
    t = _text(row)
    return '$UnsubscribeLink' in t or 'Copyright Givebacks' in t


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


def page_slug(date):
    """The page's address. `-english` because the site also archives Spanish."""
    long_date(date)                        # validates the shape
    return '%s-english' % date


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
