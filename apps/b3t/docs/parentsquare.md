# ParentSquare

## Overview

School communication platform. Posts from teachers, staff, and district. Feed is a lazy-loaded list of announcements.

## Authentication

Env vars: `PARENTSQUARE_USER`, `PARENTSQUARE_PASS`, `PARENTSQUARE_SCHOOL_ID`

Login page: `https://www.parentsquare.com/signin`
Feed URL: `https://www.parentsquare.com/schools/{SCHOOL_ID}/feeds`

Login flow: navigate to feed → if redirected to `/signin` → fill email + password textboxes → submit. No CAPTCHA or MFA.

## Commands

```bash
b3t ps login
b3t ps scan                    # print posts with full bodies
b3t ps scan --json             # JSON for scripts
b3t ps scan --since 14         # walk feed pages back 14 days
b3t ps scan --since 30 --max-pages 8
b3t ps save --dir editions/YYYY-MM-DD/submissions --since 14
```

`ps save` writes `parentsquare-{slug}.md` with title, author, date, URL, body, and extracted links.

## Scan Flow

1. Auto-login if needed
2. Navigate to the feed URL (page 1), or `?page=N` for later pages
3. Scroll twice (`mousewheel 0 3000`)
4. Click every **Read More** button to expand truncated bodies
5. Snapshot and parse (`_parse_feed_bodies`, pure, tested): each region
   holding a level-2 heading of its own name is a post → title, author,
   date, days ago, feed URL, body, links
6. With `--since N`: repeat for the next page until a page ends with a post
   older than N days or the feed has no next page. `--max-pages` (default 5)
   caps the walk; hitting it prints `PARTIAL`. Without `--since`, page 1 only.

## Pagination

The feed is paginated, ten posts a page, with a `navigation "Pagination"`
block linking `/schools/{ID}/feeds?page=N`. Before 2026-10-01 only page 1
was ever read.

## Snapshot shapes the parser must handle

- A title with a colon single-quotes the whole line:
  `- 'region "RMS: Fire Alarm ..." [ref=e1]':` (`''` is a literal quote).
- A title with a double quote escapes it: `region "Band \"shadow\"" [ref=..]`.
- The district Connections post has no `Posted by` link; its author is a
  plain `listitem: Communications`.
- The stamp is `text: • 4 hours ago • Thursday, Oct 1 at 2:55 PM •`. The
  relative half gives the age; the absolute half is stored as the date.
- Inline `strong`/`text` siblings are one sentence; links keep their href,
  with Outlook safelinks wrappers unwrapped.

The first three shapes were silently dropped before 2026-10-01 (6 of 10
posts on page 1 parsed), and the feed URL was never captured.

## Feed Structure

Top-level posts have `h2` title + `Posted by Author` link. Nested headings inside district newsletters (e.g. Connections) lack `Posted by` and are skipped.

## Editorial Notes

- **Classroom posts** (single teacher, class roster in metadata) — usually skip for Bear Tracks unless editor wants them
- **Expired posts** — skip even if still on feed
- **Band / department posts** — optional one-liner if broadly relevant
- Prefer folding PS body text into draft articles; do not write "see ParentSquare" when body was captured

## Scrolling

Two vertical scrolls per page. Older posts are on later pages, not further
down: use `--since` (and `--max-pages`), not more scrolling.

## Limitations

- No public API — browser automation only
- Very old posts need a larger `--max-pages`
- District Connections newsletter appears as one feed item; sub-articles are not split out (use `lwsd scan` for district content)
