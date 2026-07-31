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
n0b ai research --fanout 3 "Your research prompt here"
```

All arguments after `research` (and after any flags) are concatenated into a
single prompt. Put `--fanout` before the prompt — the prompt is an argparse
`REMAINDER`, so flags after the first word of the prompt are swallowed.

## How it works

1. Requires `OPENAI_API_KEY` — resolved like `n0b secrets get OPENAI_API_KEY` (env, `~/lib`, Keychain). Store it once with `n0b secrets set OPENAI_API_KEY`.
2. SHA-256 hash of the prompt (whitespace-stripped) for cache key.
3. Responses cached in `.files/research/<hash>.json` (relative to project root).
4. Submits to OpenAI Responses API or resumes from cache.
5. Polls every 30 seconds until `completed` or `failed`.
6. Final JSON on stdout (pipe to `jq`).

## Fanout (prototype)

`--fanout N` buys breadth: one deep-research job answers one angle; N parallel
jobs cover complementary dimensions and cross-check each other. Cost is roughly
**N times a single run**, plus two cheap (`gpt-4.1-mini`) calls to decompose and
merge. stderr prints job counts and a running cost estimate.

1. A cheap model call decomposes the prompt into exactly N complementary
   sub-questions (distinct dimensions — mechanism, evidence, counter-evidence,
   adoption, risks, alternatives — not paraphrases). Malformed JSON fails
   cleanly instead of fanning out garbage.
2. N background research jobs submit in parallel. Each job caches its submit
   envelope under the run directory, so an interrupted run rejoins the same
   in-flight ids on re-run.
3. Jobs poll concurrently; per-job status goes to stderr as they resolve.
4. Output lands in `.files/research/fanout-<hash>/`:
   - `meta.json` — original prompt, N, sub-questions
   - `job-1.json` … `job-N.json` — submit envelopes (rejoin cache)
   - `report-01.md` … `report-NN.md` — one sub-report per sub-question
   - `merged.md` — union of findings with per-claim `[report-NN.md | URL]`
     attribution and an explicit **Conflicts** section
5. The run directory path is printed on stdout.

Single-shot (`n0b ai research <prompt>` with no `--fanout`) is unchanged.

## Implementation

- **CLI:** `n0b ai research` / `n0b ai research --fanout N`
- **Code:** `apps/n0b/research.py` (stdlib only)
- **Model:** `gpt-5.6-sol` (research); `gpt-4.1-mini` (decompose / merge)
