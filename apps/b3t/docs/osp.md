# OurSchoolPages (PTSA Website CMS)

## Overview

CMS for the PTSA website. Used for two purposes:
1. **Scanning** for content updates (programs, calendar, club pages)
2. **Archiving** newsletter editions as pages on the site

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

### `osp archive --edition DATE --html FILE`

Creates an archive page for a sent newsletter edition.

Flow:
1. Login if needed
2. Navigate to `{OSP_BASE}/PageManager/AdminCreate/{OSP_FOLDER_ID}`
3. Set page title and URL slug
4. Paste HTML content via TinyMCE source code editor
5. Save/publish

Archive URL pattern: `{OSP_BASE}/Page/BearTracks/YYYY-MM-DD-english`

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
