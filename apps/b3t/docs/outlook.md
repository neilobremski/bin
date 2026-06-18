# Outlook (Microsoft 365)

## Overview

Email client for newsletter submissions. Content arrives in a dedicated "Submissions" folder. Also used for OTP retrieval during GiveBacks login.

## Authentication

Env vars: `OUTLOOK_USER`, `OUTLOOK_PASS`

URL: `https://outlook.office.com/mail/`

Auth check: navigate to Outlook URL → if redirected to `login.microsoftonline.com`, not authenticated.

## Commands

### `outlook check --folder NAME`

Lists messages in a folder. Default folder: "Submissions".

Flow:
1. Navigate to Outlook
2. Click folder treeitem in sidebar
3. Parse `option` elements from `listbox "Message list"`
4. Output: message number, subject, sender preview

### `outlook read N --dir PATH`

Read a specific message by number, expanding the full thread.

Flow:
1. Click message in list
2. Expand conversation (click "See more messages" if present)
3. Parse thread: `listitem` elements within conversation view
4. Parse reading pane: content inside `document "Message body"`
5. Find attachments: `option` elements with file extensions + size (e.g., "flyer.pdf 11 MB")
6. Download: click attachment → click "Download" menuitem in preview → move from `.playwright-cli/` to `--dir`

## Message Structure (Accessibility Tree)

```
listbox "Message list"
  option "Subject preview..."    ← one per message
  option "Subject preview..."

document "Message body"
  generic [ref=eNNN]: body text lines
```

## Attachments

Downloaded files land in `.playwright-cli/` (not `~/Downloads`). The `--dir` flag moves them to the specified path after download.

## Tab Management (OTP Flow)

For GiveBacks OTP retrieval:
1. `tab-new "https://outlook.office.com/mail/"` — open Outlook in new tab
2. Scan message list for "passcode" or "givebacks" keywords
3. Click message → extract 6-digit code from body
4. `tab-close` → `tab-select 0` — return to GiveBacks tab
