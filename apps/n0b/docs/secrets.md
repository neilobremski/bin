---
name: "n0b-secrets"
description: "Get, set, and trace secrets by name. Use instead of asking the user to paste keys or export env vars."
allowed-tools: Bash(n0b secrets *)
---

# n0b secrets

Named secrets with one resolution order everywhere: environment variable,
then `~/lib/<name-lower-dashes>.txt`, then the macOS Keychain.

When a secret is explicitly **set** (via `n0b secrets set`), it is *pinned*:
subsequent `get` calls return the set value even if a matching environment
variable exists. The pin list lives at `~/lib/.secret-pins`.

## Usage

```bash
n0b secrets get OPENAI_API_KEY

n0b secrets set OPENAI_API_KEY sk-...     # writes ~/lib/openai-api-key.txt (0600), pins
n0b secrets set OPENAI_API_KEY           # value from stdin (keeps it out of history)
n0b secrets set NAME --dir /some/dir     # different base directory (not pinned)
n0b secrets set NAME --keychain          # macOS Keychain (pinned)
n0b secrets set NAME --env-file .env     # upsert NAME=value line in a dotenv file (not pinned)

n0b secrets trace OPENAI_API_KEY         # show all sources and which is selected
```

`get` prints the raw value with no trailing newline; exit 1 if not found.
Destination flags on `set` are mutually exclusive.

`trace` lists every layer where the secret exists, marks which one `get`
would return, and notes whether the secret is pinned.

## Pinning rules

| `set` target       | Pinned? | Why |
|---------------------|---------|-----|
| `~/lib/` (default)  | Yes     | Explicit storage should win over ambient env |
| `--keychain`        | Yes     | Same rationale |
| `--dir /other/path` | No      | Custom dir — caller manages resolution |
| `--env-file .env`   | No      | Dotenv is a project-level concern |

## Resolution order

1. If the secret is **not pinned**: env → file → keychain.
2. If the secret is **pinned**: file → keychain → env (env is a fallback).

- **Code:** `apps/n0b/commands/secrets_cmd.py`
- Other n0b commands (e.g. `n0b ai research`) resolve their keys through
  this module, so `set` once works in sandboxes that don't inherit env vars.
