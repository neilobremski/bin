---
name: "n0b-secrets"
description: "Get and set secrets by name. Use instead of asking the user to paste keys or export env vars."
allowed-tools: Bash(n0b secrets *)
---

# n0b secrets

Named secrets with one resolution order everywhere: environment variable,
then `~/lib/<name-lower-dashes>.txt`, then the macOS Keychain.

## Usage

```bash
n0b secrets get OPENAI_API_KEY

n0b secrets set OPENAI_API_KEY sk-...     # writes ~/lib/openai-api-key.txt (0600)
n0b secrets set OPENAI_API_KEY           # value from stdin (keeps it out of history)
n0b secrets set NAME --dir /some/dir     # different base directory
n0b secrets set NAME --keychain          # macOS Keychain
n0b secrets set NAME --env-file .env     # upsert NAME=value line in a dotenv file
```

`get` prints the raw value with no trailing newline; exit 1 if not found.
Destination flags on `set` are mutually exclusive.

- **Code:** `apps/n0b/commands/secrets_cmd.py`
- Other n0b commands (e.g. `n0b ai research`) resolve their keys through
  this module, so `set` once works in sandboxes that don't inherit env vars.
