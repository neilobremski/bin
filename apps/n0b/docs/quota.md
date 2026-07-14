---
name: "n0b-quota"
description: "Check live AI tool rate-limit quotas via n0b quota. Antigravity (agy) supported today."
allowed-tools: Bash(n0b quota *)
---

# n0b quota

Check live model quota / rate limits for AI tools installed on the system.

## Usage

```bash
n0b quota              # all installed tools with quota support
n0b quota agy          # Antigravity only
n0b quota agy --json   # machine-readable JSON for scripts
n0b quota agy --raw    # JSON plus raw Antigravity API payload (debug)
```

## Antigravity (`agy`)

Sources are tried in order; the first that yields quota data wins:

1. **Live** — the local language-server API (same approach as [antigravitycli-usage-stats](https://github.com/mdsameersakib/antigravitycli-usage-stats)): find a running `language_server_*` / `agy` process, discover its localhost port(s) via `lsof` (CLI logs as fallback), POST Connect-RPC endpoints such as `GetUserStatus`. Every successful live read persists a timestamped snapshot to `~/.config/n0b/quota-agy.json`.
2. **Cloud** — when nothing is running locally, refresh the OAuth token from `~/.gemini/oauth_creds.json` and call `v1internal:loadCodeAssist` on `cloudcode-pa.googleapis.com`. Works with the app closed; returns plan/tier, plus per-model quota when the account exposes it there. The token refresh needs gemini-cli's public installed-app OAuth client pair, resolved via `n0b secrets` (they live in gemini-cli's open source but trip GitHub secret scanning, so they are not hardcoded here): one-time `n0b secrets set GEMINI_OAUTH_CLIENT_ID` and `n0b secrets set GEMINI_OAUTH_CLIENT_SECRET`.
3. **Snapshot** — last persisted state, reported with an explicit age (`as of 43m ago`).

If all three miss, the error says what to do (`try: open Antigravity IDE or run agy --continue`).

### When it works

| Session | Quota source |
|---------|--------------|
| Antigravity IDE open (language server running) | Live |
| Interactive `agy` / `agy --continue` (persistent session) | Live — includes a8s headless agents using `--continue` |
| Nothing running, logged in (`~/.gemini/oauth_creds.json`) | Cloud, else snapshot with age |
| Nothing running, never logged in, no snapshot | Actionable error |

For programmatic model switching, keep a **persistent** `agy --continue` session alive (as a8s does), then poll pool limits (same grouping as `agy /usage`):

```bash
n0b quota agy --json | jq -r '
  .groups[]
  | .name as $g
  | .buckets[]
  | select(.remaining_fraction != null)
  | "\($g)\t\(.label)\t\(.remaining_fraction)"
'
```

### Output shape

Human output mirrors `agy /usage`: **GEMINI MODELS** and **CLAUDE AND GPT MODELS** pools, each with **Weekly Limit** and **Five Hour Limit**.

The local API exposes one `quotaInfo` per model. We map it by pool the way agy presents it:

- Gemini `quotaInfo` → **Weekly Limit** (matches agy’s ~4% weekly row)
- Claude/GPT `quotaInfo` → **Five Hour Limit** (matches agy’s “Quota available” row)

Cloud-backed pool values that agy shows but the local API omits (Gemini five-hour ~90%, Claude weekly ~98%) appear as `unknown`.

### JSON shape (scripting)

Prefer `groups` for automation; `models` remains for per-model debug.

- `groups[].name` — `GEMINI MODELS`, `CLAUDE AND GPT MODELS`
- `groups[].buckets[]` — `Weekly Limit`, `Five Hour Limit`
- `groups[].buckets[].remaining_fraction` — 0.0–1.0 when known
- `models[]` — raw per-model API data
- `origin` — `live`, `cloud`, or `snapshot`; snapshots also carry `as_of` and `age`

Exit code `0` when quota data is returned; `1` on error or unavailable server.

### Limits

- Local API returns one `quotaInfo` per model; agy `/usage` also shows cloud-backed pool quotas (e.g. Gemini five-hour ~90%, Claude weekly ~98%) that appear as `unknown` here today. The `loadCodeAssist` fallback returns tier/plan but not those pool fractions for consumer accounts — filling them needs Antigravity's own session token (tracked in issue #141).
- Requires `lsof` on macOS/Linux for the live path.

## Supported tools

| Id | Tool | Requires |
|----|------|----------|
| `agy` | Antigravity (`agy` on PATH) | Running language server, or a prior login / snapshot |

More providers (Claude Code, Codex, Cursor, …) planned — see [issue #141](https://github.com/neilobremski/bin/issues/141).

## Implementation

- **CLI:** `n0b quota`
- **Module:** `apps/n0b/commands/quota_cmd.py`
