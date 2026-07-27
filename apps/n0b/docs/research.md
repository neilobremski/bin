---
name: "n0b-research"
description: "Deep research via n0b ai research (gpt-5.6-sol). Requires OPENAI_API_KEY."
allowed-tools: Bash(n0b ai research *)
---

# n0b ai research

CLI for OpenAI deep research via the Responses API (`gpt-5.6-sol`, with
`web_search_preview`) — multi-step, agentic research with source transparency.
(`o4-mini-deep-research` shut down 2026-07-23; OpenAI's replacement is
`gpt-5.6-sol`.)

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
- **Model:** `gpt-5.6-sol`
