# ParentSquare

## Overview

School communication platform. Posts from teachers, staff, and district. Feed is a lazy-loaded list of announcements.

## Authentication

Env vars: `PARENTSQUARE_USER`, `PARENTSQUARE_PASS`, `PARENTSQUARE_SCHOOL_ID`

Login page: `https://www.parentsquare.com/signin`
Feed URL: `https://www.parentsquare.com/schools/{SCHOOL_ID}/feeds`

Login flow: navigate to feed → if redirected to `/signin` → fill email + password textboxes → submit. No CAPTCHA or MFA.

## Feed Structure (Accessibility Tree)

```
region "Posts"
  heading "Post Title" [level=2]
  list
    listitem: link "Posted by Author Name"
    listitem: text: • N days ago • DayOfWeek, Mon DD at HH:MM PM •
```

**Key insight:** Only top-level posts have `"Posted by"` metadata within ~12 lines of their heading. Nested sub-headings (e.g., inside district newsletters like "Connections") do NOT have "Posted by" nearby — this differentiates real posts from newsletter content.

## Scan Flow

1. Auto-login if needed
2. Navigate to feed URL
3. Scroll down twice (`mousewheel 3000 0`) to trigger lazy-load
4. Snapshot → parse headings that have "Posted by" nearby
5. Extract: title, author, relative date

## Scrolling

Feed is lazy-loaded. Two scrolls loads ~2 weeks of posts. The `mousewheel` command's first argument is vertical (despite being labeled `dx`).

## Limitations

- No public API
- Feed is the only content endpoint
- District-level posts (Connections newsletter) appear in feed but are better captured via `lwsd scan`
- Individual post content requires clicking into it (not implemented — editor reviews manually if needed)
