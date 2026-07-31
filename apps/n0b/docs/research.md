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
n0b ai research --fanout 1 "Your research prompt here"   # same as single-shot
n0b ai research --fanout "Your research prompt here"     # N=4
n0b ai research --fanout 4 "Your research prompt here"
n0b ai research --fanout 4 --plan-only "Your research prompt here"
```

All arguments after `research` (and after any flags) are concatenated into a
single prompt. Put flags before the prompt — the prompt is an argparse
`REMAINDER`, so flags after the first word of the prompt are swallowed.

## How it works

1. Requires `OPENAI_API_KEY` — resolved like `n0b secrets get OPENAI_API_KEY` (env, `~/lib`, Keychain). Store it once with `n0b secrets set OPENAI_API_KEY`.
2. SHA-256 hash of the prompt (whitespace-stripped) for cache key.
3. Responses cached in `.files/research/<hash>.json` (relative to project root).
4. Submits to OpenAI Responses API or resumes from cache.
5. Polls every 30 seconds until `completed` or `failed`.
6. Final JSON on stdout (pipe to `jq`).

## Fanout

`--fanout` buys breadth: one deep-research job answers one angle; N background
jobs cover complementary dimensions and cross-check each other. Cost is roughly
**N times a single run**, plus cheap (`gpt-4.1-mini`) calls to plan, merge, and
citation-check. stderr prints job counts and a running cost estimate.

**Defaults and clamp:** `--fanout` absent or `--fanout 1` leaves the single-shot
path unchanged. `--fanout` with no value defaults to **N=4**. N is clamped to
**1–8** (stderr notes when clamping). Prefer `--plan-only` before spending.

### Plan (avoid anchor collapse)

Naively asking a model for N sub-questions collapses them onto the same anchor.
Instead:

1. One cheap call emits a **brief (≤200 words)** plus **12–16 candidate angles**
   (each with `angle` + `seed_query`).
2. Locally select N candidates by **MMR** (maximal marginal relevance) over
   token-level Jaccard similarity on seed queries, **λ=0** (pure diversity).
   Pure stdlib — no embeddings.
3. Each selected sub-prompt carries: objective, output format, source guidance,
   explicit boundaries (sibling angles this job does **not** cover), and a
   restatement of the top-level goal.

`--plan-only` stops after step 2: prints the brief and the N selected
sub-questions (with seed queries) and **submits zero research jobs**.

### Dispatch and resume

Background mode returns a job id immediately, so dispatch is **N sequential
POSTs**, then one poll loop over a **set** of jobs (no threads).
`max_tool_calls` scales down as N rises so width does not multiply tool spend
linearly on top of job count.

Run state lives under `.files/research/fanout-<hash>/`:

- `manifest.json` — brief, selected sub-questions, model, N, job ids, per-job state
- `job-<i>.json` — submit envelope (keyed conceptually on
  `sha256(question + fanout + model)`)
- `job-<i>-result.json` — **persisted the moment a job completes** (OpenAI
  retains background results ~10 minutes; a polled-but-unwritten result is
  money burned)
- `report-01.md` … — one sub-report per completed job
- `merged.md` — merged brief

An interrupted run **resumes** from `manifest.json`: rejoins live job ids and
skips jobs already finished.

### Partial merge and timeout

Stuck background jobs are a documented failure mode. Each job has a ~**20 minute**
timeout. Merge proceeds once **≥ ceil(N/2)** jobs are complete. The merged
output states which sub-questions are missing and why (timed out / failed) so a
partial brief is never mistaken for a complete one.

### Merge

One **serialized** cheap-model call over the completed sub-reports (not a fan-in
of many). Citations are a **URL-normalized union with corroboration counts**.
Claims are clustered across sub-reports. **Contradictions get their own section
and are surfaced, never silently resolved** — both sides appear with attribution;
the merge model is instructed not to pick a winner. A separate cheap
**citation-check** pass verifies merged claims against sources in the
sub-reports.

## Implementation

- **CLI:** `n0b ai research` / `n0b ai research --fanout [N]` / `--plan-only`
- **Code:** `apps/n0b/research.py` (stdlib only)
- **Model:** `gpt-5.6-sol` (research); `gpt-4.1-mini` (plan / merge / citation-check)
