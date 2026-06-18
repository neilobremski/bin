# b3t TODO

## High Priority

- [ ] **`givebacks regen --id UUID`** — After API push, `raw_html` is stale. Must open editor, make trivial edit (click text block, type char, delete), wait for auto-save. Without this, sent email won't match template.
- [ ] **`givebacks images --id UUID`** — List all images in the editor with index + alt text + current URL. Agent needs this to know which `--index` to pass to `upload`.
- [ ] **`givebacks delete --id UUID`** — Delete a draft via three-dot menu or API. Needed to clean up test duplicates.
- [ ] **`edition update DATE --field value`** — Update manifest fields (status, draft_id, archive_url) as edition progresses through phases.
- [ ] **SQLite content index** — `~/.b3t-content.db` indexing all gathered content across editions/sources. Tables: `content_items`, `editions`, `sources`. Commands: `b3t index rebuild`, `b3t index query --since DATE`, `b3t index duplicates`. Eliminates rediscovery cost for cheaper agents.

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
- [x] `gemini generate` — Template upload + prompt + download
- [x] Constants moved to `.env` — Source code is org-agnostic
