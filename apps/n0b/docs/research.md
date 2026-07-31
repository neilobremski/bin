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
n0b ai research --fanout "Your research prompt here"     # Stage 0 picks N
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
**N times a single run**, plus planner (`gpt-5.6-luna`) and merge (`gpt-5.6-sol`)
calls. stderr prints which N was used (recommendation vs flag), job counts, an
unverified research-job cost estimate, and a metrics block.

**Defaults and clamp:** `--fanout` absent or `--fanout 1` leaves the single-shot
path unchanged. Bare `--fanout` (no value) asks Stage 0 for
`recommended_fanout` — a simple question can come back with `1` and submit
**zero** fanout jobs. Explicit `--fanout N` always wins over the recommendation.
N is clamped to **1–8** (stderr notes when clamping). Prefer `--plan-only`
before spending.

There is **no `--depth` flag and no recursion knob**. Fanout is one level, by
design.

### Models and cost

| Role | Model | Pricing note |
|------|-------|--------------|
| Stage 0 decomposer, citation-check | `gpt-5.6-luna` | $1 / $6 per 1M input / output tokens |
| Research jobs, compose, citation attach | `gpt-5.6-sol` | Per-job figure is an **unverified estimate** (not seeded from deprecated o3 / o4-mini deep-research tables) |

### Plan (avoid anchor collapse)

Naively asking a model for N sub-questions collapses them onto the same anchor.
Instead:

1. One planner call emits a **brief (≤200 words)**, **`recommended_fanout`**,
   a **floor of 12 candidate angles** (each with `angle` + `seed_query`), and a
   dedicated **`adversarial`** angle (contradiction-seeking).
2. Locally select **N-1** candidates by **MMR** (maximal marginal relevance)
   over token-level Jaccard on seed queries, **λ=0** (pure diversity). The
   remaining slot is **reserved for the adversarial angle** and appended after
   MMR — never competed. MMR cannot surface it: a skeptic's query is lexically
   close to the topical query it attacks, so diversity selection rejects it.
3. Each selected sub-prompt carries: a restatement of the **global objective**,
   objective / output format / source guidance / boundaries, and **sibling
   assignments** with an explicit instruction not to enter a sibling's scope
   except to surface a contradiction with it.

HyDE-style query expansion is permitted for retrieval inside a research job,
but any generated pseudo-answer must be discarded and must never flow into
results or the merge input.

`--plan-only` stops after selection: prints the brief and the N selected
sub-questions (with seed queries; adversarial tagged) and **submits zero
research jobs**.

### Dispatch and resume

Background mode returns a job id immediately, so dispatch is **N sequential
POSTs**, then one poll loop over a **set** of jobs (no threads).
`max_tool_calls` scales down as N rises so width does not multiply tool spend
linearly on top of job count.

Run state lives under `.files/research/fanout-<hash>/`:

- `manifest.json` — brief, selected sub-questions, models, N, job ids, per-job
  state, metrics
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
timeout. Merge proceeds once **≥ ceil(0.67 × N)** jobs are complete (e.g. N=4
needs 3). The merged output states which sub-questions are missing and why
(timed out / failed) so a partial brief is never mistaken for a complete one.

### Merge

**Composition and citation re-attachment are separate passes** (both on
`gpt-5.6-sol`), not one call:

1. **Compose** — cluster claims into prose; surface contradictions; name
   missing sub-questions. No citation markers yet.
2. **Attach citations** — claim-level citation unions over normalized URLs.
   Normalization is conservative: do **not** collapse different document
   revisions, separate filings, a primary source and a summary of it, or
   similar-titled papers. When in doubt, keep them separate.
3. **Citation-check** (`gpt-5.6-luna`) — per claim: URL present/resolves,
   correct document, actually supports the claim, numerics/dates match, no
   citations inherited from a neighbour, conflicts still preserved.

**Contradictions** get their own section. Each side is phrased with an
observation date — "A reports X as of \<date\>" — so a disagreement that is
really a staleness gap is visible as one. Both sides are surfaced; the merge
never picks a winner.

### Metrics

At the end of a fanout run, stderr prints (and the manifest stores):

- **mean QPD** (queries per document) — warn when it drops below **0.4**
- **citation overlap ratio** across sub-reports
- **count of unique authoritative domains**
- **contradiction count**
- **total cost** — actual where the API reported usage, unverified estimate
  where it did not (labelled)

## Implementation

- **CLI:** `n0b ai research` / `n0b ai research --fanout [N]` / `--plan-only`
- **Code:** `apps/n0b/research.py` (stdlib only)
- **Models:** `gpt-5.6-sol` (research / compose / attach); `gpt-5.6-luna`
  (Stage 0 / citation-check)
