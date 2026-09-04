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
2. **Rename** subject via API PUT: `b3t gb rename --id UUID --subject "..."`
3. **Build and push** from the draft: `b3t gb build --edition YYYY-MM-DD --id UUID --push`
   (push also regenerates `raw_html`, see below)
4. **Upload images** into the slots `build` reported: `b3t gb upload --id UUID --image ... --index N`
5. **Screenshot** for review: `b3t gb screenshot --id UUID --dir ...`
6. **Send Preview** → editor reviews → **Send Now** — both are the editor's, never b3t's

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

## Building the design from draft.md

`b3t gb build` turns an edition's `draft.md` into design JSON, so nobody
hand-edits Unlayer rows or re-derives the house formatting rules per edition.

```bash
b3t gb build --edition 2026-09-06 --id UUID            # write wip/givebacks-design-new.json
b3t gb build --edition 2026-09-06 --id UUID --push     # ...and push it
b3t gb build --edition 2026-09-06 --donor old.json     # style donor from a file instead
```

The draft is the source of truth. A previous edition's design is the **style
donor**: `build` finds one row of each kind in it (h1 with the maroon band, h3,
text, image) and clones that row's padding, colors and metadata for every new
row. The masthead rows (logo, date band, header image + intro + At a Glance,
website/Spanish lines) and the standing footer (Bear Paw Fund onward) are
carried over from the donor untouched, with only the date and the intro
rewritten from the draft.

Markdown maps as: `#` to an h1 row, `###` to an h3 row, a lone `![alt](path)`
to an image row, everything else to text rows. Tables, lists, links, bold and
italic are converted inline.

**The formatting rules it encodes** (this is what used to get forgotten):

- Unlayer's `<p>` has **no bottom margin**. Consecutive paragraphs therefore
  need an explicit `<p>&nbsp;</p>` spacer between them, or the newsletter
  renders as a wall of text with no paragraph breaks.
- `<ul>` brings its own top margin, so a list never gets a spacer before it,
  and the paragraph after a list does not get one either.
- Tables get a spacer before and after.

`build --id` also carries over image URLs already uploaded to the live draft,
so a rebuild-and-repush does not blank the header or the flyers. Slots with no
URL yet are reported at the end as ready-to-run `b3t gb upload` commands with
the right `--index`.

## raw_html Regeneration

API push updates `template` but NOT `raw_html` (what gets emailed).

**`b3t gb push` now handles this.** After a successful push it fingerprints the
design, compares against the live `raw_html`, and if the sent HTML is stale it
opens the message page and clicks **Save Draft** (never Send, never Schedule),
then re-checks. It reports which of the two happened, and exits non-zero with
the manual remedy if the HTML did not catch up. `--no-save` skips the whole
step.

Comparison normalizes entities on both sides: the design stores `&nbsp;` and
`&rsquo;` while the rendered email carries the real characters, so a naive
string check reports false staleness.

Manual fallback, if the automatic save fails:
1. Open editor at `/messages/{uuid}/design`
2. Click any text block to enter edit mode
3. Type a character then delete it (net-zero edit that sets dirty flag)
4. Wait for auto-save ("Save Changes" button becomes disabled = saved)
5. Verify via API: `raw_html` contains expected content

**Important:** `space + backspace` does NOT trigger dirty flag. Must be a visible character.

Note: this Unlayer build (1.468.0) exposes no `unlayer.exportHtml`, so b3t
cannot render the HTML itself and must make the CMS do it.

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
| `mousewheel` args | Use `0 <dy>` for vertical scrolling |
| Large payloads exceed inline eval | Use `localstorage-set` as transfer buffer |
| API push doesn't update `raw_html` | `b3t gb push` detects and fixes it; fallback is editor → trivial edit → auto-save |
| push_design verify has parsing bug | Use `push` only, skip verify |
| Messages-list Duplicate menu item not found | Menu renders in a portal; click the row's kebab then the item by text via DOM |
| Satisfaction survey modal covers the messages list | Dismiss before driving the list |
