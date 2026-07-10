---
name: "n0b-research"
description: "Deep research via n0b ai research (o4-mini-deep-research). Requires OPENAI_API_KEY."
allowed-tools: Bash(n0b ai research *)
---

# n0b ai research

CLI for OpenAI's Deep Research API (`o4-mini-deep-research`) — multi-step, agentic research with source transparency.

## Usage

```bash
n0b ai research "Your research prompt here"
```

All arguments after `research` are concatenated into a single prompt.

## How it works

1. Requires `OPENAI_API_KEY` — resolved like `n0b secrets get OPENAI_API_KEY` (env, `~/lib`, Keychain). Store it once with `n0b secrets set OPENAI_API_KEY`.
2. SHA-256 hash of the prompt (whitespace-stripped) for cache key.
3. Responses cached in `.files/research/<hash>.json` (relative to project root).
4. Submits to OpenAI Responses API or resumes from cache.
5. Polls every 30 seconds until `completed` or `failed`.
6. Final JSON on stdout (pipe to `jq`).

## Implementation

- **CLI:** `n0b ai research`
- **Code:** `apps/n0b/research.py` (stdlib only)
