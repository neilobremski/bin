# The experiment ladder — design spec

*2026-07-16. Follows the verification round, the judge (#209), and the d5n
campaign. Direction set in the 2026-07-15 memo: d5n concludes, and what
replaces it is smaller, repeatable, cheaper experiments — experimentation
means MEASURING. This document designs the first four. Every open question
is resolved inline (marked DECIDED); each experiment's own protocol doc is
written from experiments/PROTOCOL.md (#187) before launch.*

*Scope: design only — no new r4t features. Every experiment below runs on
machinery that exists today.*

---

## 1. Through-line

An experiment exists to make a DECISION. Each rung below names the decision
it settles, and the ruling lands as a default in code or config — settings
over forks. The instrument stack is now real: `r4t judge <node> --json`
grades a finished run against the MAST taxonomy (calibrated 2026-07-16
against d5n ground truth), `r4t check` sweeps for forbidden patterns, the
node log carries STDOUT-REPLY/SILENT/REROUTED/QUOTA-SUSPECT events, and
velocity.csv carries turn economics. The n5a lesson stands: start small,
time-box first, stop conditions pre-written, runnable without the
orchestrating brain.

Standard shape for every rung (DECIDED):

- **Test bed:** ttt (the faux tic-tac-toe org) or two portable org dirs on
  clones of it — never a production org (s1l) and never d5n post-finale.
- **Arms:** exactly two (A/B). One variable moves; everything else frozen.
- **Replication:** 3 runs per arm on frozen rigs before any ruling; a
  one-run delta is an anecdote, not a result.
- **Time box:** wall-clock per run declared before launch; expiry = stop
  and grade what exists.
- **Grading:** pre-register expected outcomes per arm BEFORE the first
  run (the judge-calibration practice); `r4t judge --json` per run feeds
  the ledger; a human-readable delta table closes the protocol doc.
- **Rigs:** local-first (ollama-launch presets, opencode) with agy for
  any arm that needs a stronger leader; frontier harnesses only to verify
  machinery, never as the subject.
- **Hawthorne:** teams never see experiment language; org dirs and
  protocol docs live outside the workplace repo.

## 2. The ladder

Ordered by cost and by how directly the ruling feeds the three production
use cases. Rung 1 is the next thing that runs.

### E1 — Prompt adaptation: uniform vs per-rig (the production question)

- **Decision:** does the turn prompt adapt per rig, or stay one text for
  the whole roster? (Open question from the 2026-07-15 memo; directly
  shapes the Pay-i rebuild rosters, which mix copilot/claude/codex rigs.)
- **Arms:** A = today's uniform prompt. B = per-rig prompt overrides via
  the a8s definition `prompts` object — the weak rig's doctrine cut to
  the three rules it can actually hold (send with tell; reply to sender;
  commit or it doesn't exist).
- **Bed:** ttt, same 4-member roster, `dumb` (qwen3 0.6b) + `simple`
  rigs; leader on agy Flash.
- **Metrics:** judge FM-1.1 (tell-violations) and FM-2.6
  (reasoning-action mismatch) counts on the weak members;
  STDOUT-REPLY-to-staged-tell ratio; mission completion within box.
- **Hypothesis:** B halves weak-rig FM-1.1 without hurting completion.
- **Box:** 45 minutes per run.

### E2 — Terse professionalism (the cheapest rung)

- **Decision:** does the earned-doctrine line "forward-moving work is
  never polite — no thanks, no acknowledgments, no sign-offs" earn its
  prompt space? (Politeness ruled a quota-burner 2026-07-14; this measures
  the fix.)
- **Arms:** A = current prompt. B = current prompt + the terseness line.
- **Metrics:** tokens per turn (velocity.csv); ack-shaped findings
  (judge FM-1.1 acknowledgment class); rounds-to-ANSWERED on a 3-step
  delegation task.
- **Hypothesis:** B cuts tokens/turn ≥15% with no change in
  rounds-to-answer.
- **Box:** 30 minutes per run.

### E3 — Comms doctrine: open vs closed (the settings question)

- **Decision:** which comms default ships — `open` (learned addresses
  deliver) or `closed` (military reroute)? The setting shipped in the
  re-founding; the default was chosen by instinct, not measurement.
- **Arms:** A = open. B = closed. Same depth-2 roster (two cells of two
  under one lead), same mission requiring one cross-cell handoff.
- **Metrics:** hops per delegation round trip; REROUTED count; wall-clock
  to ANSWERED; judge FM-2.x (inter-agent misalignment) counts.
- **Hypothesis:** open cuts cross-cell latency ≥30% without raising
  misalignment findings — if misalignment rises instead, closed keeps the
  default and the tree tax is the price of order.
- **Box:** 60 minutes per run.

### E4 — Speed vs gates: flat vs tree (the n5a axis, controlled)

- **Decision:** when does a flat org beat a tree? n5a showed the axis
  (flat = fast + constraint-missing; tree = gate-clean + slow) but with
  confounds (different missions, a stamp bug, uneven seat attention).
  This is the controlled rerun, small.
- **Arms:** A = flat 4-member roster. B = same members, depth-2 tree.
  Identical mission with two written constraints a hasty org will break
  (a length cap and a no-new-files rule — both `r4t check`-able).
- **Metrics:** wall-clock to done; checklist findings at close (the
  constraint violations); judge FC3 (verification) counts; turns spent.
- **Hypothesis:** flat finishes ≥40% faster AND carries ≥2x the
  constraint findings — confirming the axis and giving the first
  measured basis for "flat for drafts, tree for deliverables."
- **Box:** 60 minutes per run.

### E5 — Personality-anchored judging: named persona vs anonymous rubric

- **Decision:** do review and judge agents anchor to a single well-known
  industry personality? (2026-07-16 memo: a name is a packed token — a
  whole principle-tree from the training data for the cost of two words;
  a single persona should hold stable principles where an anonymous
  rubric, or a blend of personas, answers paraphrased questions
  inconsistently.)
- **Arms:** A = the judge/review prompt as-is (anonymous rubric). B = the
  same rubric fronted by ONE named persona fitting the material (e.g. a
  famously exacting systems programmer for code review).
- **Method:** no live org needed — post-hoc, the cheapest rung, runnable
  out of order. Both arms judge the same frozen artifacts (a finished ttt
  run's captures and one code sample) through a fixed list of YES/NO
  questions that includes paraphrase pairs — the same question asked in
  different words. Three passes per arm on a mid-tier rig. Judging lists
  are yes/no flags with a why per answer, synthesized into one report —
  never essays, never analog 1-to-10 grades.
- **Metrics:** paraphrase-consistency rate (same answer on reworded
  pairs); cross-pass verdict stability; human spot-agreement on five
  sampled verdicts per arm.
- **Hypothesis:** B lifts paraphrase consistency by ≥20 points at equal
  cost; if it doesn't, names stay flavor and rubrics stay anonymous.
- **Box:** 30 minutes per arm.

## 3. Execution rules

- One rung at a time; a rung's ruling lands (default changed, doctrine
  line added or removed, README sentence updated) before the next rung
  launches. Rulings on defaults are the human's.
- Each rung gets `experiments/<name>/PROTOCOL.md` (the #187 template)
  filled in completely before launch — hypothesis, runbook, observation
  schedule, intervention table, stop conditions, ledger. A runner agent
  executes it mechanically; the orchestrating brain reads results only.
- Freeze during runs: no working-tree changes while an arm is live.
- The judge grades post-hoc only, from outside org sight, per its design.

## 4. Non-goals

- New measurement features. If a rung wants a metric that doesn't exist,
  the rung is redesigned around what exists; feature asks go to notes.
- Long-horizon missions (the novella lesson). Nothing here runs past its
  box.
- Gopher-intelligence / 100-agent swarms — a later chapter, after the
  ladder proves the method.
