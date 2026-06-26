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

Queries the **local language-server API** that Antigravity runs on your machine — the same approach as [antigravitycli-usage-stats](https://github.com/mdsameersakib/antigravitycli-usage-stats):

1. Find a running Antigravity language-server process (`language_server_*`, or embedded in `agy`)
2. Discover its localhost listen port(s) via `lsof` (and CLI logs as fallback)
3. POST Connect-RPC endpoints such as `GetUserQuotaSummary` / `GetUserStatus`
4. Parse per-model buckets (`remaining_fraction`, reset time)

No cloud calls. No credential file reads. Localhost only.

### When it works

| Session | Quota available? |
|---------|------------------|
| Antigravity IDE open (language server running) | Yes |
| Interactive `agy` / `agy --continue` (persistent session) | Yes — includes a8s headless agents using `--continue` |
| Ephemeral `agy --print` / `agy -p` | **No** — language server starts and exits with the one-shot command |

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

Exit code `0` when quota data is returned; `1` on error or unavailable server.

### Limits

- Local API returns one `quotaInfo` per model; agy `/usage` also shows cloud-backed pool quotas (e.g. Gemini five-hour ~90%, Claude weekly ~98%) that appear as `unknown` here today.
- Requires `lsof` on macOS/Linux.

## Supported tools

| Id | Tool | Requires |
|----|------|----------|
| `agy` | Antigravity (`agy` on PATH) | Running language server |

More providers (Claude Code, Codex, Cursor, …) planned — see [issue #141](https://github.com/neilobremski/bin/issues/141).

## Implementation

- **CLI:** `n0b quota`
- **Module:** `apps/n0b/commands/quota_cmd.py`
