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
b3t ps scan --since 14         # filter by "N days ago"
b3t ps save --dir editions/YYYY-MM-DD/submissions --since 14
```

`ps save` writes `parentsquare-{slug}.md` with title, author, date, URL, body, and extracted links.

## Scan Flow

1. Auto-login if needed
2. Navigate to feed URL
3. Scroll twice (`mousewheel 3000 0`) to lazy-load recent posts
4. Click every **Read More** button to expand truncated bodies
5. Run in-page extraction via `run-code`: each `[role="region"]` with `Posted by` → title, author, date, body text, links

## Feed Structure

Top-level posts have `h2` title + `Posted by Author` link. Nested headings inside district newsletters (e.g. Connections) lack `Posted by` and are skipped.

## Editorial Notes

- **Classroom posts** (single teacher, class roster in metadata) — usually skip for Bear Tracks unless editor wants them
- **Expired posts** — skip even if still on feed
- **Band / department posts** — optional one-liner if broadly relevant
- Prefer folding PS body text into draft articles; do not write "see ParentSquare" when body was captured

## Scrolling

Feed is lazy-loaded. Two scrolls loads ~2 weeks of posts. The `mousewheel` command's first argument is vertical (despite being labeled `dx`).

## Limitations

- No public API — browser automation only
- Very old posts require additional scrolling
- District Connections newsletter appears as one feed item; sub-articles are not split out (use `lwsd scan` for district content)
