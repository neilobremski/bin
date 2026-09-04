"""Build a GiveBacks (Unlayer) design from an edition's draft.md.

The newsletter's source of truth is `editions/YYYY-MM-DD/draft.md`. This module
turns that markdown into the Unlayer design JSON the GiveBacks CMS stores,
reusing a previous edition's design as the style donor so every row keeps the
padding, colors and metadata the template already carries.

Three house conventions live here so nobody has to remember them per edition:

1. Unlayer's `<p>` has no bottom margin. Consecutive paragraphs therefore need
   an explicit `&nbsp;` spacer paragraph between them or the text runs together.
2. `<ul>` brings its own top margin, so a list never gets a spacer before it,
   and the paragraph after a list does not get one either.
3. Headings are their own rows: `#` becomes an h1 row with the maroon band,
   `###` becomes a plain h3 row.
"""
import copy
import json
import re

P = '<p style="line-height: 140%;">{}</p>'
LI = '<li style="line-height: 19.6px;">{}</li>'
SPACER = P.format('&nbsp;')

# Markers that delimit the evergreen template blocks in both the draft and the
# donor design. Everything before INTRO_TAIL is the masthead; everything from
# TAIL_HEADING onward is the standing footer.
INTRO_TAIL = 'As always, remember to check out our'
TAIL_HEADING = 'Donate to the Bear Paw Fund'


class DesignError(Exception):
    """Raised when the draft or the donor design is not shaped as expected."""


# --------------------------------------------------------------- markdown

def inline(s):
    """Markdown inline formatting -> HTML."""
    s = s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    s = re.sub(
        r'\[([^\]]+)\]\(([^)\s]+)\)',
        lambda m: '<a href="{}">{}</a>'.format(m.group(2).replace('&amp;', '&'), m.group(1)),
        s,
    )
    # bare autolinks, which the escaping above turned into &lt;url&gt;
    s = re.sub(r'&lt;(https?://[^&\s]+)&gt;', r'<a href="\1">\1</a>', s)
    s = re.sub(r'&lt;(mailto:[^&\s]+)&gt;', r'<a href="\1">\1</a>', s)
    s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<em>\1</em>', s)
    return s


def _table_html(rows):
    head, body = rows[0], rows[2:]          # rows[1] is the |---| separator
    th = ''.join(
        '<th style="text-align: left; padding: 6px 8px; '
        'border-bottom: 2px solid #990000;">{}</th>'.format(inline(c))
        for c in head
    )
    out = [
        '<table style="width: 100%; border-collapse: collapse; line-height: 140%;">',
        '<thead><tr>{}</tr></thead>'.format(th),
        '<tbody>',
    ]
    for r in body:
        tds = []
        for i, cell in enumerate(r):
            cell = inline(cell)
            if i == 0:
                cell = '<strong>{}</strong>'.format(cell)
            tds.append(
                '<td style="padding: 6px 8px; border-bottom: 1px solid #dddddd;">{}</td>'.format(cell)
            )
        out.append('<tr>{}</tr>'.format(''.join(tds)))
    out += ['</tbody>', '</table>']
    return '\n'.join(out)


def md_to_html(lines):
    """Block-level markdown -> the HTML for one Unlayer text content."""
    blocks, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        if ln.lstrip().startswith(('* ', '- ')):
            items = []
            while i < len(lines) and lines[i].lstrip().startswith(('* ', '- ')):
                items.append(LI.format(inline(lines[i].lstrip()[2:].strip())))
                i += 1
            blocks.append(('ul', '<ul>\n' + '\n'.join(items) + '\n</ul>'))
            continue
        if ln.lstrip().startswith('|'):
            tbl = []
            while i < len(lines) and lines[i].lstrip().startswith('|'):
                tbl.append([c.strip() for c in lines[i].strip().strip('|').split('|')])
                i += 1
            blocks.append(('table', _table_html(tbl)))
            continue
        para = []
        while (i < len(lines) and lines[i].strip()
               and not lines[i].lstrip().startswith(('* ', '- ', '|'))):
            para.append(lines[i].strip())
            i += 1
        blocks.append(('p', P.format(inline(' '.join(para)))))

    html, prev = [], None
    for kind, chunk in blocks:
        if prev in ('p', 'table') and kind in ('p', 'table'):
            html.append(SPACER)
        html.append(chunk)
        prev = kind
    return '\n'.join(html)


# ------------------------------------------------------------ donor design

def _contents(row):
    return [c for col in row.get('columns', []) for c in col.get('contents', [])]


def _row_text(row):
    for c in _contents(row):
        if c['type'] in ('heading', 'text'):
            return ' '.join(re.sub(r'<[^>]+>', ' ', c['values'].get('text', '')).split())
    return ''


def _find_templates(rows):
    """Pick one donor row per kind out of a previous edition's design."""
    t = {}
    for row in rows:
        cs = _contents(row)
        if len(cs) != 1:
            continue
        c, kind = cs[0], cs[0]['type']
        if kind == 'heading':
            h = c['values'].get('headingType')
            band = (row.get('values', {}).get('columnsBackgroundColor') or '')
            if h == 'h1' and band and 'h1' not in t:
                t['h1'] = row
            elif h == 'h3' and 'h3' not in t:
                t['h3'] = row
        elif kind == 'text' and 'text' not in t:
            t['text'] = row
        elif kind == 'image' and 'image' not in t:
            t['image'] = row
    missing = {'h1', 'h3', 'text', 'image'} - set(t)
    if missing:
        raise DesignError(
            'donor design is missing template rows for: ' + ', '.join(sorted(missing))
        )
    return t


def _split_donor(rows):
    """head rows (masthead) and tail rows (standing footer) from the donor."""
    head_end = None
    for i, row in enumerate(rows):
        if INTRO_TAIL in _row_text(row):
            head_end = i + 1
            break
    if head_end is None:
        raise DesignError('donor design has no "%s" row' % INTRO_TAIL)

    tail_start = None
    for i, row in enumerate(rows):
        if TAIL_HEADING in _row_text(row):
            tail_start = i
            break
    if tail_start is None:
        raise DesignError('donor design has no "%s" row' % TAIL_HEADING)
    return rows[:head_end], rows[tail_start:]


# ------------------------------------------------------------------ build

class _Ids:
    def __init__(self, slug):
        self.n = 0
        self.slug = slug

    def __call__(self, prefix):
        self.n += 1
        return f'{prefix}{self.n:04d}{self.slug}'


def build(draft_md, donor_design, slug='bt'):
    """Return a new design dict: donor styling, draft.md content.

    draft_md      text of editions/YYYY-MM-DD/draft.md
    donor_design  parsed design JSON from a previous edition
    """
    design = copy.deepcopy(donor_design)
    rows = design['body']['rows']
    tpl = _find_templates(rows)
    head, tail = _split_donor(rows)
    newid = _Ids(slug)

    def clone(row):
        r = copy.deepcopy(row)
        r['id'] = newid('row')
        for col in r.get('columns', []):
            col['id'] = newid('col')
            for c in col.get('contents', []):
                c['id'] = newid('con')
        return r

    def heading_row(kind, title):
        r = clone(tpl[kind])
        _contents(r)[0]['values']['text'] = '<span>{}</span>'.format(inline(title))
        return r

    def text_row(html):
        r = clone(tpl['text'])
        _contents(r)[0]['values']['text'] = html
        return r

    def image_row(source_path):
        r = clone(tpl['image'])
        v = _contents(r)[0]['values']
        v['src'] = {'url': '', 'width': 1200, 'height': 1200,
                    'dynamic': False, 'autoWidth': True}
        v['_pending_upload'] = source_path
        return r

    # ---- parse the draft
    md = re.sub(r'<!--.*?-->', '', draft_md, flags=re.S)     # drop TODO comments
    lines = md.split('\n')

    date = None
    m = re.search(r'^\*\*.*?\*\*\s*\|\s*(.+?)\s*$', md, flags=re.M)
    if m:
        date = m.group(1).strip()

    try:
        intro_end = next(i for i, l in enumerate(lines) if l.startswith(INTRO_TAIL))
    except StopIteration:
        raise DesignError('draft has no "%s" line' % INTRO_TAIL)
    # the intro starts after the header image line, which is the last image
    # reference above the At a Glance block
    img_lines = [i for i, l in enumerate(lines[:intro_end])
                 if re.match(r'^\[?!\[', l.strip())]
    if not img_lines:
        raise DesignError('draft has no header image line above the intro')
    intro_start = img_lines[-1] + 1
    intro_html = md_to_html(lines[intro_start:intro_end])

    try:
        body_start = next(i for i, l in enumerate(lines)
                          if l.startswith('# ') and i > intro_end)
    except StopIteration:
        raise DesignError('draft has no article headings after the intro')
    try:
        body_end = next(i for i, l in enumerate(lines)
                        if l.startswith('## ') and TAIL_HEADING in l)
    except StopIteration:
        raise DesignError('draft has no "## %s" line' % TAIL_HEADING)

    body_rows, buf = [], []

    def flush():
        if any(l.strip() for l in buf):
            body_rows.append(text_row(md_to_html(buf)))
        buf.clear()

    for ln in lines[body_start:body_end]:
        img = re.match(r'^!\[([^\]]*)\]\(([^)]+)\)\s*$', ln.strip())
        if ln.startswith('# '):
            flush()
            body_rows.append(heading_row('h1', ln[2:].strip()))
        elif ln.startswith('### '):
            flush()
            body_rows.append(heading_row('h3', ln[4:].strip()))
        elif img:
            flush()
            body_rows.append(image_row(img.group(2)))
        else:
            buf.append(ln)
    flush()

    # ---- assemble
    new_head = [clone(r) for r in head]
    for r in new_head:
        for c in _contents(r):
            if c['type'] == 'heading' and date:
                c['values']['text'] = '<span>{}</span>'.format(date)
            elif c['type'] == 'text' and INTRO_TAIL not in _row_text(r):
                c['values']['text'] = intro_html
                break
    design['body']['rows'] = new_head + body_rows + [clone(r) for r in tail]
    return design


def carry_image_urls(design, live_design):
    """Copy image URLs from the live draft into a freshly built design.

    A rebuild produces empty image slots. Without this a re-push would blank
    the header and any flyers that were already uploaded to the CMS.
    """
    live = [c for r in live_design['body']['rows'] for c in _contents(r)
            if c['type'] == 'image']
    new = [c for r in design['body']['rows'] for c in _contents(r)
           if c['type'] == 'image']
    if len(live) != len(new):
        return 0, 'image count differs (live %d, built %d)' % (len(live), len(new))
    carried = 0
    for c, src in zip(new, live):
        url = src['values'].get('src', {}).get('url', '')
        if url:
            c['values']['src'] = copy.deepcopy(src['values']['src'])
            c['values'].pop('_pending_upload', None)
            carried += 1
    return carried, None


def pending_uploads(design):
    """Image slots still waiting on a `b3t gb upload`, in placeholder order."""
    out = []
    idx = 0
    for row in design['body']['rows']:
        for c in _contents(row):
            if c['type'] == 'image':
                if not c['values'].get('src', {}).get('url'):
                    out.append((idx, c['values'].get('_pending_upload', '?')))
                idx += 1
    return out


def outline(design):
    """One line per row: index, content types, first text. For eyeballing."""
    lines = []
    for i, row in enumerate(design['body']['rows']):
        kinds = '+'.join(c['type'] for c in _contents(row))
        lines.append(f'{i:3d} {kinds:22s} {_row_text(row)[:60]}')
    return '\n'.join(lines)
