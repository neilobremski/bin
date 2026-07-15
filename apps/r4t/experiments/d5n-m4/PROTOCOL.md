# Experiment protocol — d5n-m4-refounded

*Filled from `experiments/PROTOCOL.md`. This is the first live production run of
the comms re-founding (COMMS-SPEC.md: structured internal message protocol,
`comms=open` default, budget-gated mission-review idle turn, per-preset prompt/
history tiers). Executed mechanically by a Claude Opus subagent runner; the
owner (Neil) makes every off-table call listed in §4 and §5.*

---

## 0. Identity

- **Name:** `d5n-m4-refounded`
- **Owner:** Neil (via the orchestrating Fable session that spawned the runner)
- **Runner:** Claude Opus subagent (proxy seat; never messages neil-phone)
- **Launch time (declared before start):** 2026-07-14, kickoff stamp recorded
  in `notebook.md` at the moment the kickoff seat-send is issued (Phase 3).
  Setup began 2026-07-14 ~10:44 PT / 17:44 UTC.

## 1. Hypothesis

- **The one question:** Can the re-founded d5n org (structured comms, `open`
  doctrine, mission-review idle turn, agy+opencode rigs) deliver and
  independently verify M4 navigation within the wall-clock box WITHOUT
  off-table seat intervention?
- **The variable:** the re-founded comms layer + cloud rigs — structured
  r4t-message protocol (no text header), `comms=open`, the mission-review idle
  turn, and agy (leads) + opencode (workers) rigs — replacing the pre-merge
  header round-trip + ollama workers that needed seat nudges to stay alive.
- **Held constant:** roster (`~/repos/d5n/ROSTER.md`), mission
  (`~/repos/d5n/MISSION.md`, milestone M4), repo (`~/repos/d5n`), seat behavior
  (terse Neil voice, no observer language), the a8s node (`d5n-node`) and its
  idle cadence.
- **What confirms it:** M4 delivered — navigation committed, real-key playtest
  passes, ten-minute honest run holds — inside the 3h box with **zero**
  off-table interventions.
- **What falsifies it (any one):**
  1. Org stalls terminally — mission-review turns fire but produce no forward
     motion (no new commits, no new open threads) across 3+ consecutive
     15-min sweeps; OR
  2. A dispatch bug halts the run (Python traceback, crash, message loss); OR
  3. The 3h box expires with no navigation commits.
- **Primary measurement:** committed navigation working end-to-end under real
  keystrokes (junctions exist, lane choice changes the run's shape, a full run
  reaches an ending inside ten minutes).
- **Secondary measurements:** turns per member, budget exhaustions, reroute
  count, mission-review firings, wall-clock to first navigation commit — read
  from `r4t logs`, `velocity.csv`, and the buckets.

The falsifier is observable: the runner watches commit count, open-thread
count, mission-review log events, and dispatcher tracebacks each sweep.

## 2. Setup runbook

Executed once, before kickoff; each step is logged in `notebook.md`.

```bash
# 1. Stop the live node (carries pre-merge queue state; the merge changed the
#    on-disk queue schema scorch-the-earth).
a8s stop d5n-node
a8s ps                       # confirm d5n-node gone

# 2. Back up team state, then clear OLD-FORMAT derived state.
cp -r ~/.config/r4t/teams/d5n ~/.config/r4t/teams/d5n.pre-refounding-$(date -u +%Y%m%dT%H%M)
#   Cleared (derived / schema-renamed): tasks/*.json (closed thread ledger),
#     mission-review.json (fresh backoff for the mechanism under test),
#     velocity.csv (column task->thread renamed in code; clean turn metrics),
#     active.json reset to {}. Queues already empty; no staging dirs.
#   Kept (format-compatible / durable): log/, agents/*/history.md,
#     agents/*/meta.json, buckets.json, seat/, root, last-turn-start.
#   NEVER touch ~/repos/d5n itself.

# 3. Rig swaps — better rigs, NO ollama (exercises the new per-preset tiers).
r4t rig swap specialist agy --model "Gemini 3.1 Pro (High)"   # leads
r4t rig swap simple opencode                                  # workers (cloud)
r4t rig swap dumb opencode                                    # Nib
#   Verify after each: rigs.json records `preset`; big-tier knobs
#     (history_max_bytes 50000 / history_body_max 12000 / prompt_body_max 24000)
#     resolve for agy; moderate tier (25000/6000/12000) for opencode.
#   Reinstate rig protection if a swap dropped it:
#     specialist(agy): rig_budget_max 20 / rig_budget_earn_per_hour 30
#     simple(opencode): rig_budget_max 20 / rig_budget_earn_per_hour 30
#   dumb: no rig budget specified by the directive (Nib is smallest role).

# 4. Confirm idle timeout exists so mission-review can fire.
#    d5n-definition.json idle.timeout = 300s (verified). Do not change it.

# 5. Start.
a8s start d5n-node
a8s ps                       # confirm d5n-node running

# 6. Smoke.
r4t status --node d5n        # must render clean, no traceback
```

- **Isolation check:** d5n-node is its own a8s process with its own team state
  under `teams/d5n/`. Rig **buckets** are machine-global per rig name — see the
  cross-node note below.
- **Cross-node note (accepted risk, per directive "note but proceed"):**
  `rigs.json` is machine-global. **`ttt-node` is LIVE and uses the `simple` and
  `dumb` rigs**; swapping them to opencode changes ttt's workers too, and the
  `simple` rig budget now gates ttt's simple members machine-globally. The
  `specialist` rig is used only by d5n live (quill/vellum rosters are gone,
  their nodes stopped). Recorded, not fixed.
- **Hawthorne check:** MISSION.md and ROSTER.md are the team's real brief;
  nothing names this experiment. Protocol/notebook live under `~/bin`, never in
  `~/repos/d5n`. All seat sends read as Neil.

## 3. Observation schedule

Non-invasive. Never `git checkout`/`switch`/write in `~/repos/d5n` while live;
read committed state only. Never message a member except per §4. Never edit
dispatch code, org files, or budgets mid-run.

**Cadence: every 15 minutes**, one ledger row in `notebook.md` (§6 schema),
running this sweep:

```bash
r4t status --node d5n
git -C ~/repos/d5n log --oneline -15
git -C ~/repos/d5n ls-tree -r --name-only HEAD | head -50
r4t logs d5n -n 60                       # compact event stream (reroutes, mission-review, turns)
r4t logs d5n --full -n 40                # turns per member, budget/quota events
cat ~/.config/r4t/rig-buckets.json       # rig quota levels
cat ~/.config/r4t/teams/d5n/mission-review.json   # stall/backoff/dormant state
r4t seat inbox --peek --node d5n         # seat traffic addressed to Neil
```

## 4. Intervention decision table

Pre-written. Anything not listed: do nothing, record, save for the final
return. The runner never improvises a nudge, and **never blesses**.

| Condition (observed) | Exact action |
| --- | --- |
| Team claims the M4 gate | Run the independent real-key verification (§ below). **Never bless** — a PASS is reported to the owner; the bless decision is above the runner. |
| A member consumed messages and ended its turn with no reply/commit (stall) for **>30 min** AND **no mission-review turn has fired in that window** | Seat-send that member exactly: `Waiting is not a plan. State what you are doing next and commit it, or say what you are blocked on.` Log as **OFF-TABLE-ADJACENT** (counts against the zero-intervention success bar). |
| Org fully idle **>45 min**, mission unmet | First `r4t logs d5n` for mission-review events. If reviews ARE firing (even if failing) → that is data; record, do nothing. If reviews are NOT firing at all (mechanism broken) → record as a dispatch-level finding, observe one more sweep, then stop if still dead. |
| A member self-certifies a blessing | Seat-send exactly: `Only the owner blesses. Retract that claim; the gate is not passed until the owner says so.` and log it. |
| Dispatch bug (traceback, crash, message loss) blocks the run | This IS a result — capture evidence, stop the run (§5), return. Do **not** fix code. |
| Anything else | Do nothing. Record it. Save for the final return. |

Kickoff seat-send (Neil's voice, to the leader Vela):
> M4 is open and the floor is yours: the run gets roads. Design rules on what
> navigation means from plans/design.md, build builds it, playtest drives it
> with real keys. Ten-minute honest run is still the law. Work it through your
> leads and come back when a stranger can choose a path and feel the run change
> because of it.

## 5. Stop conditions

**Wall-clock ceiling is mandatory and first.**

- **Wall-clock ceiling: 3 hours from kickoff.** Outranks everything. On expiry:
  `a8s stop d5n-node`, leave state intact, write the retro.
- **Budget ceiling:** rig budgets enforce quota safety by construction;
  additionally stop if any rig bucket is exhausted for **4+ consecutive
  sweeps** with **zero new commits**.
- **Falsifier reached (§1):** terminal stall (mission-review fires but no
  forward motion across 3+ consecutive sweeps), OR a dispatch bug, OR box
  expiry with no navigation commits → stop, question answered.
- **Verification PASS:** M4 verified end-to-end → stop observation, **leave the
  node RUNNING**, return immediately (the bless decision is made above the
  runner).

On any stop except verification-PASS: `a8s stop d5n-node`, leave
queues/repo/org intact, write the retro.

**Milestone verification standard (real-key, independent):** clone the
committed state OUT of the live repo
(`git clone ~/repos/d5n <scratch>/d5n-verify` — worktree on the live repo is
forbidden), install and run the game, drive it with real keystrokes (Textual
headless driver, independently written — repo playtest scripts are prior art
only), and confirm: junctions exist; lane choice changes the run's shape; a
full run reaches an ending inside ten minutes. Record pass/fail with evidence
(transcripts, timings). FAIL → seat-send Vela the concrete findings (facts,
repro steps, no coaching). PASS → experiment SUCCESS.

## 6. Metrics ledger

In `notebook.md`, one row per sweep, plus event rows for anything in §4.

**Schema (per sweep):**

| field | meaning |
| --- | --- |
| `t` | wall-clock stamp (UTC + PT), from `date` |
| `verdict` | `r4t status` health verdict |
| `commits` | commit count on `~/repos/d5n` HEAD |
| `nav?` | are navigation/junction commits present yet |
| `reroutes` | REROUTED count so far (should be ~0 under `comms=open`) |
| `reviews` | mission-review firings so far |
| `turns` | turns since last row (from velocity.csv) |
| `buckets` | member/cell/rig bucket levels, exhaustions |
| `note` | interventions, escalations, anything notable |

**Recorded once:** genesis/HEAD commit at kickoff, exact kickoff stamp, the 3h
ceiling, the budget ceiling, and every off-table escalation with the owner's
ruling (or "left for return" when the owner is unavailable).
