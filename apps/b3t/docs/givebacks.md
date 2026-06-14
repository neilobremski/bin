# GiveBacks / Unlayer

## Architecture

- **Frontend:** JS SPA at `{GIVEBACKS_BASE}` (env var)
- **API:** `https://api.givebacks.com` (platform-level, same for all orgs)
- **Editor engine:** Unlayer, loaded in a cross-origin iframe
- **Image hosting:** `https://s3.us-east-1.amazonaws.com/unlayer.memberhub/{timestamp}-{filename}`
- **Org identifier:** `GIVEBACKS_CAUSE_ID` env var (UUID)

## Authentication

Env vars: `GIVEBACKS_USER`, `GIVEBACKS_PASS`

Login flow:
1. Navigate to `/messages` — if page loads, already authenticated
2. If redirected to `/users/sign_in` — fill email/password, submit
3. If redirected to `/one-time-passcode` — OTP sent to email
4. OTP handling: open Outlook in new tab, find code, close tab, enter 6 digits, check "Trust this browser", submit

Session persists via Chrome profile. Trust cookie avoids OTP on subsequent logins.

## Messages API

```
GET/PUT https://api.givebacks.com/services/communication/messages/{uuid}?cause_id={CAUSE_ID}
```

Auth: cookies from browser session. Fetch with `credentials: "include"` from a GiveBacks-origin page.

**Response fields:** `message.template` (Unlayer JSON as string), `subject`, `uuid`, `status`, `raw_html`, `raw_text`, `recipients`, `sent_at`

**Update:** `PUT {message: {template: jsonString}}` or `{message: {subject: "new title"}}`

**Large payloads:** Use `localStorage` as transfer buffer — `localstorage-set` then fetch from localStorage in eval.

## Newsletter Lifecycle

1. **Duplicate** previous edition (three-dot menu → "Duplicate")
2. **Rename** subject via API PUT
3. **Pull** design JSON → modify programmatically → **Push** back
4. **Open editor** after push (loads fresh from server)
5. **Upload images** via UI (see below)
6. **Regen raw_html** — trivial edit in editor triggers auto-save
7. **Send Preview** → editor reviews → **Send Now**

## Image Upload (UI Automation)

Direct click on `<img>` elements fails because Unlayer's `.blockbuilder-layer-selector` overlay divs intercept pointer events. Solution: coordinate-based clicking.

**Deterministic flow:**
1. Navigate to editor (`/messages/{uuid}/design`)
2. Resize viewport: `resize 1600 1000`
3. Get image bounding box via `run-code` (locator in Unlayer iframe):
   ```js
   async function main() {
     const frames = page.frames();
     for (const f of frames) {
       if (f.url().includes("unlayer")) {
         const imgs = await f.locator("img[alt]").all();
         await imgs[N].scrollIntoViewIfNeeded();
         const box = await imgs[N].boundingBox();
         return JSON.stringify(box);
       }
     }
   }
   ```
4. Click center coordinates: `page.mouse.click(cx, cy)` — hits overlay, selects block
5. Snapshot → find `button "Upload Image"` ref in right panel
6. Chain: `click {upload_ref} && upload "/absolute/path/to/file"`

**Key constraints:**
- `scrollIntoViewIfNeeded()` required for images below the fold
- Click and upload MUST be chained (`&&`) — file chooser is only pending briefly
- Refs change after every action — always re-snapshot
- Use absolute file paths

## raw_html Regeneration

API push updates `template` but NOT `raw_html` (what gets emailed). To regen:
1. Open editor at `/messages/{uuid}/design`
2. Click any text block to enter edit mode
3. Type a character then delete it (net-zero edit that sets dirty flag)
4. Wait for auto-save ("Save Changes" button becomes disabled = saved)
5. Verify via API: `raw_html` contains expected content

**Important:** `space + backspace` does NOT trigger dirty flag. Must be a visible character.

## Design JSON Structure

Schema version 12:
```
design.counters         — content type counters
design.body.id          — body ID
design.body.rows[]      — array of row objects
design.body.values      — body styles (fontFamily, textColor, backgroundColor, contentWidth: 600)
```

**Row:**
```json
{"id": "...", "cells": [1], "columns": [{
  "id": "...",
  "contents": [{"id": "...", "type": "image", "values": {...}}],
  "values": {"padding": "0px", "backgroundColor": ""}
}], "values": {"backgroundColor": "", "padding": "0px"}}
```

**Content types:** `text`, `heading`, `image`, `button`, `divider`, `html`

**Image src structure:**
```json
{"url": "https://s3....", "width": 2752, "height": 1536, "id": 40206112,
 "filename": "header.jpg", "contentType": "image/jpeg", "size": 5764950,
 "dynamic": false, "autoWidth": true}
```

## Push-then-Save Race Condition

Unlayer editor keeps an in-memory copy of the design. If editor is open during API push, saving overwrites the push.

**Correct sequence:** Close editor → Push via API → Open editor fresh → Trivial edit → Auto-save.

## Known Issues

| Issue | Solution |
|-------|----------|
| Overlay blocks `click` on images | Use coordinate-based `page.mouse.click(x, y)` |
| `type` command escapes `!` as `\!` | Use `eval 'el => el.textContent = "..."'` |
| `Meta+a` selects entire block | Use eval on specific element ref |
| Refs change after every action | Fresh snapshot before every interaction |
| `run-code` can crash session | Prefer built-in commands + `&&` chaining |
| `mousewheel` args reversed | First arg = vertical despite `dx` label |
| Large payloads exceed inline eval | Use `localstorage-set` as transfer buffer |
| API push doesn't update `raw_html` | Open editor → trivial edit → auto-save |
| push_design verify has parsing bug | Use `push` only, skip verify |
