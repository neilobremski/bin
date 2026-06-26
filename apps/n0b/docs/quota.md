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

For programmatic model switching, keep a **persistent** `agy --continue` session alive (as a8s does), then poll:

```bash
n0b quota agy --json | jq -r '
  .models[]
  | select(.remaining_fraction != null)
  | "\(.label)\t\(.remaining_fraction)"
'
```

Pick a model whose `remaining_fraction` is still above your threshold, or fall back to another provider when the lowest bucket hits zero.

### JSON shape (scripting)

Key fields:

- `models[].label` — display name (e.g. `Gemini 3.5 Flash`)
- `models[].model_id` — backend id when present
- `models[].remaining_fraction` — 0.0–1.0 aggregate across buckets
- `models[].buckets[]` — per-window limits (`Hourly` / 5h, `Weekly` when exposed)
- `models[].buckets[].remaining_fraction` — fraction left in that window
- `models[].buckets[].reset_time` — ISO timestamp for next reset

Exit code `0` when quota data is returned; `1` on error or unavailable server.

### Limits

- **Weekly quotas** are often not exposed by the local API (same limitation as the reference VS Code extension).
- Requires `lsof` on macOS/Linux.
- IDE sessions expose `--csrf_token` on the `language_server` command line; CLI-embedded servers use HTTP localhost without that flag.

## Supported tools

| Id | Tool | Requires |
|----|------|----------|
| `agy` | Antigravity (`agy` on PATH) | Running language server |

More providers (Claude Code, Codex, Cursor, …) planned — see [issue #141](https://github.com/neilobremski/bin/issues/141).

## Implementation

- **CLI:** `n0b quota`
- **Module:** `apps/n0b/commands/quota_cmd.py`
