# Microsoft Forms

## Overview

Newsletter submission form hosted on Microsoft Forms. Responses are downloaded as Excel (`.xlsx`) and parsed for new submissions since the last edition.

## Authentication

Uses Microsoft 365 session (same as Outlook). Env vars: `OUTLOOK_USER`, `OUTLOOK_PASS`

Env var: `FORMS_URL` — full URL to the form's design page (contains the form ID).

## Commands

### `forms download --edition YYYY-MM-DD`

Downloads the submissions Excel file to `editions/{date}/wip/submissions.xlsx`.

Flow:
1. Navigate to `{FORMS_URL}&analysis=true` (responses view)
2. Dismiss "Got it" popup if present (find ref via snapshot, not text-based click)
3. Click "More options" button (the one for Responses, not individual questions)
4. Click "Download a copy" menuitem
5. Wait for the downloaded `.xlsx` in `.playwright-cli/` or `~/Downloads` (optionally filter with `FORMS_DOWNLOAD_PREFIX` in `.env`)
6. Move to edition wip directory

### `forms list --edition YYYY-MM-DD [--since DATE] [--json]`

Parse the downloaded Excel and filter/display submissions.

Uses `openpyxl` to read the xlsx. Columns include: Start time, Name, Email, Article title, Article body, Image attachment.

`--since` filters by submission date (Start time column).

## Auth Check

Navigate to `forms.office.com` → follow the SSO redirects and check whether they settle on a Microsoft Forms host or the login page.

## Gotchas

- The "Got it" popup on first visit can't be dismissed by text-based click — must use ref from snapshot
- "More options" button exists in multiple places — need the one for the whole Responses section, not per-question
- CDP-attached Chrome downloads to `~/Downloads`; a playwright-cli managed browser uses `.playwright-cli/`
- Forms reply-to is broken: replies to the confirmation email do NOT reach the newsletter editor. Contact submitters directly for attachments.
