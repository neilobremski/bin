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

Env vars: `OURSCHOOLPAGES_USER`, `OURSCHOOLPAGES_PASS`, `OSP_BASE`, `OSP_FOLDER_ID`

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

The archive LISTING at `/Page/BearTracks/Archive` is a separate page and is not
updated by this command. It has no 2026-2027 section yet.

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
