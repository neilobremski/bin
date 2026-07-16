# LAB-SPEC — `r4t lab`: repo-bundled, repeatable experiments

*2026-07-16. Direction from Neil: build on the `r4t sandbox` foundation so
that anyone can run any experiment built into the repo, assuming they have
access to the rigs and models those experiments declare. A good experiment
runs several times and shows a consistent pattern; results likely change
with model caliber, so experiments constrain themselves to specific models.
Companion docs: plans/EXPERIMENT-LADDER.md (what to test),
experiments/PROTOCOL.md (the per-experiment procedure template, #187).*

## 1. What exists and what's missing

`r4t sandbox` already proves the hard parts: a hermetic run (private
`A8S_HOME` + `R4T_HOME` in a temp dir, nothing shared with the operator's
real state), a bundled seed (GOAL.md, ROSTER.md, rigs.json), registration
through the real a8s CLI, run-to-quiescence, graceful teardown with a
verified no-orphans invariant, and one self-contained markdown report whose
MECHANICAL CHECKS section is computed rather than claimed. That is a trial
harness in everything but name — but it runs exactly one hardcoded
scenario, records nothing durable, and knows nothing about arms,
replication, or environment pinning.

`r4t lab` generalizes it. `r4t sandbox` remains what it is today — the
plumbing smoke check ("does my install work end to end") — and its
internals become the shared chassis.

## 2. Vocabulary and verb

- **Experiment** — a question packaged in the repo under
  `apps/r4t/experiments/<name>/`: manifest + protocol + fixtures. Names are
  ladder rungs (`e5a-persona-presence`) or freeform slugs.
- **Arm** — one condition (A/B). One variable moves between arms.
- **Trial** — ONE hermetic execution of one arm. Trials are cheap and
  numerous; a single trial proves nothing by design.
- **Ledger** — the append-only record of every trial of an experiment on
  this machine. Aggregation reads the ledger, never a single report.

Verb: **`lab`** — a subcommand namespace mirroring `rig`'s shape:

```
r4t lab list                     # experiments bundled in this repo + rig/model prereq status
r4t lab run <name> [--arm A] [-n N] [--rig ROLE=RIG] [--fake]
                                 # N trials of an arm (default: 1 trial, each arm alternating);
                                 # --rig rebinds a role to another local rig for this run
r4t lab report <name>            # aggregate the ledger: pattern-over-N, prediction scoring
r4t lab ledger <name> [--json]   # raw trial rows
```

(Considered `r4t trial <name>` as the verb; rejected — the noun "trial" is
better spent naming the unit inside lab vocabulary, and a namespace verb
matches the existing `rig`/`seat` grammar.)

## 3. The experiment package

```
apps/r4t/experiments/<name>/
  experiment.json    # machine-readable manifest (the runnable encoding)
  PROTOCOL.md        # the #187 template, filled in (the human procedure)
  fixtures/          # frozen inputs: seed repo, captures to judge, code samples
  arms/<arm>/        # per-arm overrides: ROSTER.md, prompt overrides, judge prompt
```

`experiment.json` (JSON, matching a8s house style):

```json
{
  "name": "e5a-persona-presence",
  "question": "Does fronting the judge rubric with one named persona raise paraphrase consistency?",
  "class": "posthoc",                  // "posthoc" (judge frozen fixtures) | "org" (live hermetic org per trial)
  "arms": {"A": "anonymous rubric", "B": "single named persona"},
  "roles": {                           // who the experiment needs, bound to rigs (Neil 2026-07-16:
                                       // rigs are the swappable alias for judges AND participants,
                                       // exactly as ROSTER.md Rig: lines bind members)
    "judge": {"rig": "specialist", "pin": "Gemini Flash"}
                                       // rig = DEFAULT symbolic rig name, satisfied from the local
                                       // rigs.json and swappable at run time:
                                       //   r4t lab run <name> --rig judge=<other-rig>
                                       // pin = the model series the experiment INTENDS; the trial
                                       // records what the bound rig actually resolves. A rig that
                                       // resolves outside the pin series still runs but is flagged
                                       // and lands in its own report column (never pooled).
  },
  "trials_per_arm": 6,                 // default N for `lab run` without -n
  "stopping_rule": null,               // optional pre-registered early-stop (e.g. sequential
                                       // "wins - losses >= 2*sqrt(N)"); null = fixed N.
                                       // Chosen BEFORE the first trial — peeking-then-stopping
                                       // inflates false positives.
  "box_seconds": 1800,                 // hard wall-clock per trial
  "metrics": ["paraphrase_consistency", "verdict_stability"],
  "predictions": [                     // pre-registered BEFORE first trial; scored by `lab report`
    {"claim": "B paraphrase consistency exceeds A by >= 20 points", "confidence": 0.7}
  ]
}
```

The manifest is the *encoding*; PROTOCOL.md remains the *procedure* — the
runbook, observation schedule, intervention table, and stop conditions a
human or agent reads. `lab run` refuses to run an experiment whose
PROTOCOL.md still contains `<...>` placeholders (pre-registration is not
optional).

## 4. The trial (one `lab run` execution)

Chassis extracted from sandbox.py into a shared module (`lab.py` owns the
loop; sandbox.py becomes a thin caller of the same chassis):

1. **Probe prereqs** — every manifest role binds to a rig (the manifest's
   default name, or the `--rig role=name` override) that exists in the
   local rigs.json and resolves (preset exists, model resolves via the
   preset's resolver, harness binary on PATH). Missing → refuse with a
   `(try:)` hint, exit 2. Probe-only, per the ratified isolation
   principle: r4t never fixes the operator's setup.
2. **Capture environment** — recorded into the trial row before anything
   runs: harness CLI versions (`agy --version`, etc.), the manifest's
   model **pin** (a stable slug like `"Gemini Flash"`) and the **resolved**
   model the rig actually serves for it right now (via the preset's live
   resolver), rig config snapshot, r4t git describe, OS. Cloud models
   cannot be frozen — the pin names the series, the resolution stamps the
   data point (see §5).
3. **Hermetic setup** — temp `A8S_HOME`/`R4T_HOME`; fixtures copied in;
   arm overrides applied. `org`-class: seed repo cloned per trial (genesis
   hash recorded), org registered, kicked off exactly as sandbox does
   today. `posthoc`-class: no org at all — the judge rig is invoked over
   the frozen fixtures per the arm's judge prompt.
4. **Run inside the box** — `box_seconds` is mandatory and outranks
   everything (the n5a lesson, codified in PROTOCOL.md §5).
5. **Grade mechanically** — the sandbox MECHANICAL CHECKS pattern: every
   metric in the manifest is computed by code from artifacts (judge
   `--json` output, logs, ledger events), never claimed by an agent.
6. **Teardown** — sandbox's no-orphans invariant, temp dirs removed,
   nothing left but the trial row and the per-trial report file.
7. **Append the ledger row** — `~/.config/r4t/lab/<name>/trials.jsonl`
   (machine state, not repo): trial ULID, stamp, arm, environment capture,
   seed, metrics, exit reason, wall-clock, report path, **sha256 of each
   raw output artifact** (the "prompt-run card" minimum-reporting
   practice: identity + params + output hashes), and an `excluded` flag
   with reason when a trial is dropped (parse error, timeout) — exclusions
   are recorded, never silently discarded. Raw outputs are kept verbatim
   beside the report in `reports/`.

Determinism honesty: LLM trials are stochastic; the ledger records seeds
where the fixture layer honors them (seeded repo genesis, fixed fixture
order), and consistency comes from replication, not from pretending the
model is deterministic.

## 5. Aggregation and prediction (`lab report`)

Reads the ledger, groups by arm × recorded model, and prints a sectioned
panel (p0o style):

- **Pattern** — per metric: per-arm distribution over N trials, effect
  size (difference in means/proportions) with a bootstrap confidence
  interval, plus a paired sign test (win count) — the two tools the
  field recommends for 3–10 stochastic runs, in place of p-value worship
  (methodology research 2026-07-16: "Measuring all the noises of LLM
  Evals" — prediction noise typically exceeds data noise, so replication
  washes out randomness). Judge-agreement metrics use Cohen's κ (two
  raters) or Fleiss κ / Krippendorff α (more), with **κ ≥ 0.6 as the
  floor for calling a judge configuration reliable**. Pairwise
  comparisons MUST randomize presentation order per trial (position
  bias).
- **Verdict line** — `consistent (N=6/6 same direction)` /
  `mixed — needs more trials` / `falsified`. The manifest's falsifier and
  the report's verdict use the same computed metrics.
- **Predictions** — each pre-registered prediction scored against the
  aggregate (held / falsified / undecided) with a running Brier score per
  prediction across report runs, so accumulated experiments sharpen
  forecasting — the "recording, aggregating, then making predictions"
  loop. Prediction confidences are the *operator's* (or the orchestrating
  agent's), stated at pre-registration — never the subject model's
  self-reported confidence, which the literature finds poorly calibrated.
- **Caliber as an axis, not a caveat** (Neil, 2026-07-16) — a manifest rig
  entry pins a stable **slug** (the identity you subscribe to: `"Gemini"`,
  `"Gemini Flash"`), and every trial records the **resolved** model the
  rig actually served (`Gemini 3.5 Flash (High)` today, `Gemini 4 ...`
  after an upgrade). Trials are aggregated per resolved model within the
  slug's series — never blended across resolutions — and the report plots
  the series side by side: this point is Gemini (3.5), this point is
  Gemini (4), same or different. Re-running a decided experiment after a
  model upgrade is therefore not contamination but the point: the ledger
  becomes a longitudinal record of whether a ruling survives model
  generations — regression testing for doctrine. A second *family*
  (Claude, local qwen) is still how a ruling graduates from "true on
  Gemini" to "true".

## 6. Who runs it

- A human: `r4t lab run e5a-persona-presence -n 6` and read the report.
- A runner agent: executes PROTOCOL.md, which for lab-class experiments
  reduces to the same command plus the observation schedule.
- CI or a stranger's machine: `lab list` shows which experiments their
  rig/model access can satisfy; `--fake` runs the chassis end-to-end with
  the deterministic fake agents for zero-cost validation of the
  experiment package itself.

## 7. The pre-codification research gate

Before any experiment package is committed to the repo (Neil,
2026-07-16): run a deep-research pass on that experiment's specific
question — is the problem observed in the wild, and what hypotheses,
experiments, and solutions have others published? The gate does not block
*running* the lab on a draft package; it blocks *committing* it. Two
payoffs: (a) external comparison data the report can cite next to our
ledger ("the field predicts B; our N=6 says..."), and (b) borrowed
material — prompts, verification questions, metrics worth adding or
swapping before the design freezes. The research result and its synthesis
land in the experiment's directory alongside PROTOCOL.md (scrubbed:
public repo), so the package carries its own literature context.

## 8. Ships as

1. **PR 1 — chassis + verb:** extract sandbox internals into the shared
   chassis, add `lab {list,run,report,ledger}`, manifest schema + loader,
   environment capture, trials ledger, `--fake` path green in tests.
2. **PR 2 — first packaged experiment:** `e5a-persona-presence` (posthoc
   class, cheapest, no live org) with fixtures frozen from a finished ttt
   run, PROTOCOL.md filled, predictions pre-registered, and its
   pre-codification research round done (builds on the 2026-07-16
   value-prop round's judging findings — PersonaEval, pairwise >
   pointwise — with a targeted pass on persona-anchored judging).
3. Ladder rungs E1–E4 package one at a time as they come up (org class),
   each its own small PR, each through the research gate first.

## 9. Non-goals

- No new measurement features (ladder rule stands): metrics compute from
  what exists — judge `--json`, logs, velocity.csv, git.
- No experiment autodiscovery beyond the experiments/ dir; no remote
  registry.
- No attempt to freeze cloud models; record and partition instead.
- `r4t sandbox` UX unchanged (it may gain "backed by the lab chassis"
  internals but keeps its verb, flags, and report).
