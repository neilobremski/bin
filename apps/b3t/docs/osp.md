# OurSchoolPages (PTSA Website CMS)

## Overview

CMS for the PTSA website. Used for two purposes:
1. **Scanning** for content updates (programs, calendar, club pages)
2. **Archiving** newsletter editions as pages on the site (see also
   `b3t gb archive`, which produces the HTML this consumes). `gb archive
   --edition` is now optional: omitted, the edition date is read from the
   newsletter's own date heading and cross-checked against the Givebacks
   send date (0-3 days apart; sends often land a day or more after the date
   printed in the edition). Given explicitly, it is used unchanged.

## Authentication

Env vars: `OURSCHOOLPAGES_USER`, `OURSCHOOLPAGES_PASS`, `OSP_BASE`, `OSP_FOLDER_ID`,
`OSP_LISTING_PAGE_ID`

Login URL: `{OSP_BASE}/Account/LogOn`

## Commands

### `osp scan`

Scans multiple site pages for content that could be newsletter-worthy:
- Home, Programs, Volunteer, Calendar
- Club pages (Drama, Science, Math, Quiz Bowl, Spelling Bee)
- About, Spirit Gear, Reflections

**Calendar parsing:** Grid layout with alternating date rows (7 cells = days of week) and event rows (events positioned by column index).

### `osp archive --edition DATE --html FILE [--save]`

Creates the archive page for a sent edition from the file `gb archive` wrote.
Fills the form and STOPS by default; `--save` publishes.

Form is at `{OSP_BASE}/PageManager/AdminCreate/{OSP_FOLDER_ID}`. Fields, all
named (do NOT hunt for them in the a11y snapshot; OSP puts each label on its
own line above its box, and Google Translate injects 5 decoy text inputs):

| Field | id | Value |
|---|---|---|
| Name (URL slug) | `#PageName` | `YYYY-MM-DD-english` |
| Folder | `#PageFolderDropDown` | BearTracks = 6282 (preselected by the URL) |
| Heading (page title) | `#PageHeading` | `September 20, 2026 [English]: Bear Tracks - Meet the Teachers` |
| Is Hidden | `#IsHidden` | unchecked |
| Content | `#Html` (TinyMCE) | the archive HTML |
| Mobile content | `#MobileHtml` | empty on every existing page |
| Save | `#SaveButton` | |

Content goes in via `tinymce.get("Html").setContent(html)` then `.save()`.
Refuses to save if the editor lost rows or the folder is not BearTracks, and
refuses outright if a page already exists at that slug.

Sign-in: `#EmailAddress`, `#Password`, submit is `form input[type=image]`.

**TinyMCE rewrites what it is given.** It converts `style="width:580px"` to
`width="580"`. Pages made in 2025 have zero `style=` attributes; content pushed
through `setContent` keeps them, so recent pages look like the email (maroon
heading bars, colours) and older ones look like plain site content. Both work.

Archive URL pattern: `{OSP_BASE}/Page/BearTracks/YYYY-MM-DD-english`

The archive LISTING at `/Page/BearTracks/Archive` is a separate page. It is not
updated by this command: see `osp listing` below.

**Save is verified, not assumed.** A plain `page.click('#SaveButton')` followed
by a fixed timeout can look like it worked without having submitted anything:
nothing after the click proves the click did anything. Seen live, the actual
cause is OSP's own "Leave this page?" prompt, raised because the page is
dirty and Save is itself a navigation. `_save()` (shared with `osp listing`)
registers a one-shot `page.once('dialog', ...)` handler that accepts before
clicking (an `addEventListener('beforeunload', ...)` handler survives
`window.onbeforeunload = null`, so the handler is the actual fix, not the
assignment), then waits for the navigation `#SaveButton` causes on a real
save (to `/Home`) and reports failure (`no-nav`) when none comes. See
`docs/session.md` for the same handling built into `session.navigate()`.

### `osp listing --edition DATE [--html FILE] [--save]`

Adds one edition's entry to the archive LISTING page (`/Page/BearTracks/Archive`,
CMS page id `OSP_LISTING_PAGE_ID`, edit URL `{OSP_BASE}/PageManager/Edit/{OSP_LISTING_PAGE_ID}`).
Fills the form and STOPS by default; `--save` publishes. `--html` defaults to
`editions/DATE/wip/archive.html`, same file `osp archive` takes and `gb archive`
writes; the entry's title comes from that file's `.meta.json`, and its
highlights from `archive.highlights()` (the At a Glance list, capped at 8).

Content is TinyMCE, same as `osp archive`: read with `ed.getContent()`, write
with `ed.setContent(html); ed.save();`, passed through the same localStorage
bridge for size.

The page's structure: one `<h5>Bear Tracks Archive for YYYY-YYYY</h5>` per
Aug-Jul school year (an edition dated 2026-08-24 is `2026-2027`; one dated
2026-05-31 is `2025-2026`), newest year first, each followed by a `<ul>` of
entries, newest edition first:

```html
<h5>Bear Tracks Archive for 2026-2027</h5>
<ul>
<li><a href="https://rmsptsa.org/Page/BearTracks/2026-09-20-english">September 20, 2026 [English]: Bear Tracks - Meet the Teachers</a>
<ul>
<li>Curriculum Night Sep 22</li>
</ul>
</li>
</ul>
<p>&nbsp;</p>
```

All of this (finding/creating the section, inserting in date order, the
duplicate check) is pure-HTML logic in `archive.listing_insert()`, testable
without a browser. It:
- refuses (nothing computed further, nothing saved) if the edition's slug is
  already linked anywhere on the page;
- inserts in date order within the section, normally at the top;
- creates a new section, immediately before the newest existing one, the
  first time an edition from a new school year is added.

`osp listing` itself additionally refuses if the archive page it would link
is not live yet (same check `osp archive` uses to refuse creating a page that
already exists, applied in reverse).

**Before writing anything**, the current listing HTML is backed up to
`editions/DATE/wip/listing-backup.html`, and the path is printed. With
`--save`, after the save navigation succeeds, the PUBLIC page is re-fetched
(cache-busted) and checked: the new slug must be linked, and the number of
`-english"` links must have gone up by exactly one. Either check failing is a
loud error naming the backup path: nothing is assumed to have worked just
because the Save click did.

## Site Structure

Pages scanned (typical PTSA site):
- `/` — Home (announcements, featured content)
- `/Page/Programs` — After-school programs
- `/Packet/Volunteer` — Volunteer opportunities
- `/Page/Calendar` — Event calendar grid
- `/Page/Clubs/{name}` — Individual club pages
- `/Page/BearTracks/Archive` — Newsletter archive listing

## Calendar Grid

The calendar uses an HTML table where:
- Date rows have 7 cells containing day numbers
- Event rows have event text positioned in the column corresponding to the day of week
- Parser tracks current row's days and maps events by column position
