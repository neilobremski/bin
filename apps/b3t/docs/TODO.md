# b3t TODO

## High Priority

- [ ] **`givebacks regen --id UUID`** — After API push, `raw_html` is stale. Must open editor, make trivial edit (click text block, type char, delete), wait for auto-save. Without this, sent email won't match template.
- [ ] **`teams sweep` does not read channels** — Chats parse. A channel click (`General`, `PTSA Communications`) leaves the message pane empty: no `chat-pane-item`, no `time` elements, and the only other frame is an empty `about:srcdoc`. Chats carry the traffic today, so this is a gap, not a blocker. Needs the channel post DOM identified.
- [ ] **`givebacks images --id UUID`** — List all images in the editor with index + alt text + current URL. Agent needs this to know which `--index` to pass to `upload`.
- [ ] **`givebacks delete --id UUID`** — Delete a draft via three-dot menu or API. Needed to clean up test duplicates.
- [ ] **`edition update DATE --field value`** — Update manifest fields (status, draft_id, archive_url) as edition progresses through phases.
- [ ] **SQLite content index** — `~/.b3t-content.db` indexing all gathered content across editions/sources. Tables: `content_items`, `editions`, `sources`. Commands: `b3t index rebuild`, `b3t index query --since DATE`, `b3t index duplicates`. Eliminates rediscovery cost for cheaper agents.

- [ ] **`gb subscribe` write contract** — Captured the real Add Contact form
  2026-09-17. Three fixes needed: (1) send the wrapped `cause_backer` payload
  with explicit `in_directory: false`, not a flat body; (2) treat an
  "Anonymous" response as a shielded-address FAILURE and name it, instead of
  reporting a bare 201 as success; (3) use `/contacts` and warm the app
  through `/dashboard` first, because a wrong admin path returns an empty
  shell that shimmers forever rather than a 404. Evidence in
  `editions/2026-09-20/wip/subscribe-queue.md`.

- [ ] **Scrapers drop link URLs** — `ps save` and `ol read` both emit link
  TEXT and discard the href. The RMS band fundraiser came through as "Car Wash
  Fundraiser:" with no link, and the Math Club submission lost all three of its
  pages. Both had to be recovered by hand from the live DOM, and one of them
  only existed inside a QR code on an image. Any submission whose value IS the
  link is silently gutted. Emit `[text](href)` for anchors in both.

- [ ] **Multi-line bold breaks the builder** — `**bold**` spanning a line break
  in draft.md reaches the newsletter as literal asterisks, because the markdown
  is converted one line at a time. Caught only by reading the preview email on
  2026-09-17; the draft had to be reflowed by hand. `md_to_html` should join a
  paragraph's lines before converting emphasis.

## Medium Priority

- [ ] **`osp archive --edition DATE --html FILE`** — Has code, never tested end-to-end. Needs verification.
- [ ] **`givebacks preview --id UUID --email ADDRESS`** — Send a test email preview via API or UI.
- [ ] **`outlook send --draft FILE`** — Send a board review draft from a prepared markdown/HTML file.
- [ ] **Automated link validation** — Curl all URLs in design JSON before send. Catch 404s.
- [ ] **Automated sync check** — Extract headings from draft.md and design JSON, diff for mismatches.

## Low Priority / Future

- [ ] **Convert article bodies to `paragraph` content type** — Unlayer's Lexical JSON (`textJson`) gives better UI editing. Needs HTML→Lexical converter.
- [ ] **Fix legacy boilerplate grammar** — Evergreen sections had grammar issues; verify in current template.
- [ ] **Standardize all links to `https://`** — Some legacy links use `http://`.
- [ ] **Clean up stale `textJson` placeholders** — Heading blocks have `"text":"Heading"` in Lexical JSON. Harmless, cosmetic.

## Done

- [x] `givebacks login` — Auto-login with OTP handling
- [x] `givebacks list` — List recent drafts/sent via API
- [x] `givebacks pull/push` — Design JSON via API with localStorage buffer
- [x] `givebacks duplicate --id UUID` — Three-dot menu → Duplicate → return new UUID
- [x] `givebacks rename --id UUID --subject "..."` — Update subject via API PUT
- [x] `givebacks upload --id UUID --image FILE --index N` — Coordinate-based click + chained upload; waits for S3 save (size-scaled timeout)
- [x] `givebacks screenshot --id UUID` — Full-page PNG of newsletter page
- [x] `parentsquare scan` — Feed scan with full bodies (`--json`, `--since`)
- [x] `parentsquare save --dir PATH` — Write `parentsquare-*.md` submission files
- [x] `lwsd scan` — RMS + district events/news (no auth)
- [x] `osp scan` — Site pages + calendar parsing
- [x] `peachjar list/get` — GraphQL queries (no browser)
- [x] `forms download/list` — Excel download + openpyxl parse
- [x] `outlook check/read` — Folder scan + thread expansion + attachments
- [x] `givebacks subscribe --email ADDRESS [--name NAME] [--dry-run]` — Creates or re-subscribes a contact under the `backer` service and verifies by reading it back. Sends no mail.
- [x] `teams list/sweep/save` — Chat sweep with author, local timestamp and body
- [x] `whatsapp list/sweep/save` — Group chat sweep via `data-pre-plain-text`
- [x] `gemini generate` — Template upload + prompt + download
- [x] Constants moved to `.env` — Source code is org-agnostic

## From September 2026 production run

- [x] **Masthead slots are named, not counted** — The Sep 20 edition went out
  to preview with the new header above the date and the previous edition's
  header below it, because `gb upload --index 0` counts images in the editor
  and image 0 is the fixed logo. `gb build` now fills both masthead slots from
  the draft's own image lines, in order.
- [x] **`gb images --edition D --id UUID`** — Uploads every image the built
  design is waiting on, in slot order, and skips ones already in the CMS.
  Replaces working out an `--index` by hand.
- [x] **Uploads are edition-qualified** (`20260920-header.jpg`), so a rebuild
  can no longer carry last edition's masthead just because both files were
  called `header.jpg`. `carry_image_urls` matches by name, not position.
- [x] **`gb upload --expect-empty`** refuses a slot that already holds an
  image; Unlayer's own placeholder art counts as empty.
- [x] **`ol reply --match NAME --file body.md`** — Reply All in the original
  thread, saved as a draft, never sent. Subscribe and unsubscribe answers
  belong on the request they answer; `ol draft` stays for board mail only.
- [x] **An open composer took down the next command** — Outlook's
  unsaved-changes prompt is an event listener, so clearing `onbeforeunload`
  does not stop it, and the standing dialog blocked every later call with
  "does not handle the modal state". `session.run` now dismisses a stuck
  dialog and retries once, and every folder switch leaves the composer first.
- [x] **`ol read --dir` never downloaded anything** — it looked for a Download
  menuitem (it is a button) and then for the file in `.playwright-cli` (it
  lands in ~/Downloads).
- [ ] **`ol reply` cannot answer a thread that already holds a draft** — it
  stops rather than adding a second one. Editing the existing draft would be
  better.
- [ ] **Message list is virtualised** — `--match` scrolls the list to find a
  message further down. Outlook search would be steadier than wheel events.

## From August 2026 production run
- [ ] **`gb duplicate` broken twice over**: (1) the Mantine kebab menu opens on
  mousedown and closes on mouseup, so playwright-cli `click` never leaves the
  dropdown open — needs DOM `.click()` on `button[aria-haspopup=menu]` in the
  target `<tr>`, ~3s wait, then DOM `.click()` on the Duplicate item in
  `[id$=-dropdown]`; (2) `_find_kebab_for_message` measures the button before
  table layout settles (x off by ~1000px). Workaround verified in production.
- [x] `gb upload` bbox run-code timeout raised 10s → 30s (OOPIF frame
  enumeration regularly exceeded 10s; cleared on retry before the fix).
- [x] `session.ensure_running()` now cycles the browser when
  `document.visibilityState == "hidden"` (zero-window Chrome makes the Unlayer
  OOPIF ignore all input; bringToFront/AppleScript don't fix it).
- [x] `gemini` upload flow rewritten for the "Upload & tools" menu (locator
  click via run-code + CLI `upload` for the file-chooser modal); download
  watcher now also checks ~/Downloads (CDP Chrome) with mtime filtering.
- [x] `forms` auth check accepts forms.cloud.microsoft / forms.microsoft.com;
  download watcher checks ~/Downloads with mtime filter + 30s poll.
- [ ] `ol read N` silently re-reads the previous message when the reading pane
  doesn't refresh; `ol check` ignores `--folder` and can report a stuck filter
  ("0 messages" until the filter chip is cleared via snapshot+click).
- [ ] `lwsd scan` returns nav chrome, not content (needs content-area scoping);
  the news-article body doesn't render in snapshots (JS hydration).
