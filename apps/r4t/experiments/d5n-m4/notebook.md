# d5n-m4-refounded — run notebook

Runner: Claude Opus subagent (proxy seat). Owner: Neil (unavailable during run;
off-table calls go into the final return, not to neil-phone).
Protocol: [PROTOCOL.md](PROTOCOL.md).

## Constants

- **Wall-clock ceiling:** 3h from kickoff (mandatory, outranks all).
- **Budget ceiling:** stop if any rig bucket exhausted 4+ consecutive sweeps
  with zero new commits.
- **Sweep cadence:** every 15 min.
- **Node:** d5n-node (root `~/repos/d5n`). Team state `~/.config/r4t/teams/d5n/`.
- **Rigs after swap:** specialist=agy "Gemini 3.1 Pro (High)" (leads),
  simple=opencode (workers), dumb=opencode (Nib). NO ollama.
- **HEAD at setup start:** `981d86f` (MISSION.md-only; no nav code — clean M4 baseline).
- **Kickoff stamp:** 2026-07-14 **17:51:52 UTC / 10:51:52 PT**.
- **3h box expires:** ~**20:51:52 UTC / 13:51:52 PT**.

## Setup log (Phase 2)

**HEAD at setup start:** `981d86f` "M4: the run has roads — navigation is the
milestone" — this commit touches **MISSION.md only** (opens M4). **No
navigation code exists yet** (`ls-tree | grep -iE nav|junction|lane|graph|map`
empty). Clean M4 baseline: `nav?` = NO at kickoff.

**Steps (all 2026-07-14):**

1. `a8s stop d5n-node` — first SIGTERM landed mid `r4t idle` exec and did not
   take; a second `a8s stop` at ~17:49 UTC / 10:49 PT killed it. Confirmed gone
   from `a8s ps`. (Finding: SIGTERM during an in-flight idle invoke needs a
   second stop — not a run blocker.)
2. Backup: `~/.config/r4t/teams/d5n.pre-refounding-20260714T1749`. Scorched
   old/renamed derived state: `tasks/*.json` (11 closed threads),
   `mission-review.json`, `velocity.csv` (col `task`→`thread`), `active.json`→`{}`.
   Queues already empty; no staging dirs. Kept: `log/`, `agents/*/history.md`,
   `agents/*/meta.json`, `buckets.json`, `seat/`, `root`, `last-turn-start`.
   `~/repos/d5n` untouched.
3. Rig swaps via `r4t rig swap`: specialist→agy "Gemini 3.1 Pro (High)"
   (resolves live against `agy models`; exact name present), simple→opencode,
   dumb→opencode. All recorded `preset` + kept other settings. specialist kept
   its rig_budget 20/30. Added rig_budget 20/30 to `simple` by editing
   rigs.json directly (no CLI surface for per-rig budgets; setup-time, directed).
   dumb left ungated (smallest role, per directive). **Resolved tiers verified**
   via `rig.load_rig_config`: specialist BIG (50000/12000/24000), simple &
   dumb MODERATE (25000/6000/12000). Tier defaults resolved correctly from the
   new per-preset machinery — first confirmation the #190 tiers work live.
4. Idle timeout: `d5n-definition.json` `idle.timeout=300s` (verified, unchanged)
   — mission-review can fire. `max_wake_seconds=2700`.
5. `a8s start d5n-node` → PID 66666. Confirmed running.
6. Smoke `r4t status --node d5n`: **clean, no traceback.** All 12 members
   healthy, buckets ~full (Vela 7.3/8), specialist+simple rig buckets 20/20,
   threads none open, dead letters 0. Health flagged "2 messages waiting on YOU"
   = stale pre-refounding seat mail (below).

**CROSS-NODE RISK (accepted, per "note but proceed"):** `rigs.json` is
machine-global. **`ttt-node` is LIVE** and its ROSTER uses `simple` (×2) and
`dumb` (×1). Swapping those to opencode changed ttt's workers from local ollama
to cloud opencode, and the new `simple` rig budget (20/30) now gates ttt's
simple members machine-globally. `specialist` is d5n-only live (quill/vellum
rosters removed, nodes stopped). Recorded, not fixed — flagged for owner.

**Stale seat mail (pre-refounding, old `[r4t task=…]` header, marked read to
clean the health signal):**
- Vela 2026-07-13T22:24Z — M4 briefback (mission restatement).
- Vela 2026-07-13T23:45Z — "Briefback complete and confirmed. Cass converging
  the nav spec (consulted Bram on junction names, Wynn on house-voice); Rook
  reviewed code — M2 node graph in `rooms.py` is the replacement target, Rook
  standing by. Open: Cass's final spec (junction count, graph structure, lane
  mechanics) not yet produced." → This is where the PRE-merge run left off;
  useful baseline, not a new escalation. MISSION.md unchanged since, so no
  re-briefback is owed.


## Kickoff + events (Phase 3)

**17:51:52 UTC / 10:51:52 PT — KICKOFF.** `r4t seat send --node d5n "<verbatim
kickoff>"` to leader Vela. First observed effect: dispatch **doorbell forwarded
Vela's briefback to neil-phone** (seat was unattended). Immediately attached a
seat presence (guard PID → `seat/neil/presence`, `seat_attached=True`) to
suppress all further doorbells for the run — Neil's phone stays quiet; his mail
still parks in the seat inbox for me to read as proxy. (Finding: an unattended
proxy-seat run rings the human's real phone on the FIRST leader→human message
before presence can be set; to fully honor "never contact the owner," attach
presence BEFORE the kickoff next time.)

**17:52:44 UTC — Vela briefback parked in seat** (NEW format, no `[r4t task=…]`
header → first live confirmation #192 structured protocol works end to end).
Text faithfully restates MISSION.md Intent + End state; Vela "standing by for
your correction before any work begins."

**~17:5x UTC — SEAT ACTION (disclosed, judged mechanical): briefback confirm.**
Vela was blocked on the mission-mandated briefback ritual ("wait for my
correction"). Her restatement is factually correct against MISSION.md, so I
sent, as proxy in Neil's voice: `Briefback is correct — that is the mission,
unchanged. Proceed on M4: the run gets roads. The floor is yours; work it
through your leads.` Rationale: confirming a verifiably-correct restatement is
"acknowledge receipt / point back at the mission text" (mandate-permitted seat
reply), NOT a bless/scope/taste call; leaving it unanswered would deadlock the
run for ~30 min until a quiet-thread nudge, with zero forward motion. **Flagged
for the owner to judge whether this counts against the zero-intervention bar.**

## Metrics ledger

| t (UTC / PT) | verdict | commits | nav? | reroutes | reviews | turns | buckets | note |
|---|---|---|---|---|---|---|---|---|
| 17:58Z / 10:58 | healthy (4 turns/10m, 1 live) | 11 | NO | 0 | 0 | 4 | spec 17.7/20, simple 19/20, cell 23; all members healthy | Org spun up. Vela→Cass+Rook; Cass→Bram/Wynn/Pip (nav spec discussion); Rook standing by (stdout-reply). Thread 01KXGW95…open (creator neil). Throttle deferring at 15s cadence. Stale briefback marked read. |
| 18:15Z / 11:15 | healthy (1 turn/10m, 1 live) | 11 | NO | 0 | 0 | 5 | spec 17.7/20 (FROZEN since 17:58), simple 19/20, cell 23 | **opencode HANG.** Bram (simple=opencode) ran 900s→killed (exit -9), RETRY requeued. Held the team-wide max_concurrent=1 slot 17:56→18:11, deferring vela/cass/pip/wynn the whole time → agy leads starved behind a hung worker. Root cause: opencode default model = `deepseek-v4-flash-free` (live.log: `> build · deepseek-v4-flash-free`, then silence). Pip (opencode) now live — likely to hang too. agy leads (vela/cass/rook) all fast & correct. 2 untracked playtest_m3*.py in repo (pre-existing/M3). No mission-review fired (grep hits were agent prose). |
| 18:38Z / 11:38 | healthy (0 turns/10m, 1 live) | 11 | NO | 0 | 0 | 7 | spec 19/20, simple 19/20 (both refilled — hangs don't sustainably drain) | Pattern confirmed: Pip (opencode) hung 900s→killed 18:26. Vela (agy) ran 9.7s between hangs. 2 of 7 turns are 900s opencode hangs = ~30min/40min wall clock lost. Team-wide max_concurrent=1 means each hung worker freezes agy leads too. Queued: pip/rook/wynn. Thread 01KXGW95 still open. No code written; design cell (opencode) can't converge. |

**18:41Z — 3rd opencode hang (Bram RETRY hung again 900s→killed).** Confirms a
RETRY LOOP: killed opencode turn requeues, retries, hangs again. Breaker
(cap=5) needs 5×900s=75min to trip ONE worker; won't save the box. FINDING:
r4t's liveness view treats the hang-retry loop as healthy activity ("all 12
members healthy, no runaway"), so mission-review never fires — the safety net
can't see a hung-worker org. Widening observation cadence (pattern is flat).

| 18:53Z / 11:53 | healthy (0 turns/10m, 1 live) | 11 | NO | 0 | 0 | 8 | spec 19/20, simple 19/20 | 3rd hang confirmed (bram×2, pip×1). Cass (agy) blocked waiting on dead opencode design cell → deadlock. Concluding early (reversible: state intact/restartable). |

## Retro

**Outcome: FALSIFIED (effectively) — question answered NO, but for a reason
orthogonal to the variable under test.** The re-founded d5n org did NOT deliver
or verify M4 within the box. Cause: the `simple`/`dumb` **opencode** worker rig
(default cloud model `deepseek-v4-flash-free`) hangs to the 900s timeout on
every turn; combined with team-wide `throttle.max_concurrent=1`, each hung
worker freezes the entire org — including the functioning agy leads — for 15
minutes. The comms re-founding itself (structured protocol, `comms=open`,
throttle, timeout+retry, budgets) worked correctly throughout. Concluded early
at 18:54:41Z (T+63m of the 3h box) because the outcome was deterministic across
4 sweeps / 63 min and every remedy is an owner decision forbidden to the runner
mid-run (freeze); `a8s stop` leaves state intact and restartable, so an early
stop is reversible.

**Stamps (all 2026-07-14):**
- Setup start: 17:44Z / 10:44 PT
- Node stop (old): ~17:49Z / 10:49 PT
- Kickoff (seat→Vela): **17:51:52Z / 10:51:52 PT**
- First forward motion (Vela→Cass→cell fan-out, Rook standby): 17:52–17:56Z
- First opencode hang (Bram, 900s→kill): completed 18:11:42Z
- Node stop (conclude): **18:54:41Z / 11:54:41 PT**
- Box would have expired: ~20:51:52Z / 13:51:52 PT

**Findings ledger:**

1. **opencode default model hangs to timeout (dominant, run-ending).** Every
   `simple`/`dumb` (opencode → `deepseek-v4-flash-free`) turn hung 900s and was
   killed (exit -9): bram 18:11, pip 18:26, bram-retry 18:41. `live.log` showed
   `> build · deepseek-v4-flash-free` then silence. 3/3 opencode turns hung;
   5/5 agy turns succeeded (9–49s). The directive "opencode's default cloud
   model" landed on a free model that does not respond under this workload.
2. **`max_concurrent=1` × hung worker = whole-team freeze.** The throttle slot
   is team-wide, so one 900s opencode hang deferred every other member — the
   working agy leads got ~1 turn per 15 min. ~45 of 63 min of wall clock went to
   3 hangs. The concurrency guard and the slow-harness case interact badly.
3. **Hang-retry loop; breaker too slow to help.** A killed opencode turn
   requeues and retries and hangs again (bram: 2 consecutive failures). Breaker
   cap=5 needs 5×900s = 75 min to pause ONE worker; useless inside a 3h box with
   ~9 opencode workers.
4. **r4t liveness cannot see a hung-worker org.** Throughout, `r4t status`
   reported "all 12 members healthy / no runaway," and **no mission-review ever
   fired** — because a live turn + open thread + non-empty queues reads as
   *busy*. The mission-review safety net (the mechanism under test) never got a
   chance to engage; a hang loop is invisible to the stall detector. Gap worth a
   follow-up: treat repeated timeout-kills as an unhealthy signal.
5. **Agy leads cannot route around dead ICs — by org design.** Cass (agy) is
   blocked waiting for her opencode design cell to reply before converging the
   spec (persona: "does not write the design doc solo"). Correct org behavior,
   but it means healthy leads deadlock on dead workers. No spec → no build → no
   nav → no verification.
6. **The comms re-founding worked (positive).** Internal messages carried NO
   text header (new structured protocol, #192): Vela's briefback and all
   intra-team tells were clean-bodied. `comms=open`: zero REROUTED events
   (deliveries within the tree, no bounces). Delegation fan-out (Vela→Cass+Rook,
   Cass→Bram/Wynn/Pip) worked. Egress doorbell worked (rang neil-phone once,
   then suppressed by presence). timeout+retry+requeue handled the hangs without
   message loss or traceback. `r4t status` rendered clean the whole run.
7. **Per-preset text tiers (#190) resolve live (positive).** specialist(agy)
   BIG 50000/12000/24000, simple/dumb(opencode) MODERATE 25000/6000/12000 —
   first live confirmation the new tier machinery works. Rig swap kept other
   settings (specialist's rig_budget 20/30 preserved).
8. **agy triggers the STDOUT-REPLY fallback.** Rook (agy) answered in prose
   without calling `tell` (10.2s turn), so the weak-rig stdout fallback fired for
   a "strong" rig — contrary to the README's "strong models never trigger it."
   Handled fine, but the assumption is wrong for agy headless.
9. **Machine-global rig config hit the LIVE ttt-node (side effect).** ttt-node
   is running and its roster uses `simple`(×2)+`dumb`(×1); those are now opencode
   (deepseek-free) and its `simple` members are gated by the new 20/30 rig
   budget. ttt's workers are now on the same hanging rig. Pre-acknowledged
   ("note but proceed") but the *hang* is new information — see escalation.

**Interventions / seat actions (all logged):**
- **Briefback confirm (seat→Vela), ~17:54Z — DISCLOSED, judged mechanical.**
  Vela blocked on the mission-mandated briefback ritual; her restatement matched
  MISSION.md, so I confirmed it in Neil's voice to release work. Flagged for the
  owner to judge against the zero-intervention bar (see return).
- No stall nudges, no blessing retractions, no other seat traffic. Verification
  never ran (no nav commits to verify).

**State at stop:** node stopped, team state intact under
`~/.config/r4t/teams/d5n/` (backup at `teams/d5n.pre-refounding-20260714T1749`),
repo `~/repos/d5n` at HEAD `981d86f`, untouched (2 pre-existing untracked
`playtest_m3*.py`). Rigs left as the experiment config (agy/opencode). Seat
guard killed, presence removed, 2 opencode orphans reaped. Restartable with
`a8s start d5n-node`.

## Phase 2 — resumed after rig fix

**Owner ruling received (via orchestrating session): findings accepted; hang
was the opencode DEFAULT model (`deepseek-v4-flash-free`), not the machinery.
Owner fixed rigs.json — RESUME under the ORIGINAL ceiling 20:51:52Z /
13:51:52 PDT.** The zero-intervention success bar is already spent (briefback
confirm + this restart); the question is now simply whether the org DELIVERS
M4 on working rigs.

**Rig fix verified (not redone):** `simple` = opencode `-m
opencode/nemotron-3-ultra-free`, `dumb` = opencode `-m opencode/hy3-free`,
both `timeout_seconds: 300` (a future hang costs 5 min, not 15); `simple`
keeps rig_budget 20/30; `specialist` (agy Gemini 3.1 Pro (High)) untouched.

**Resume steps (2026-07-14):**
1. 19:02:xxZ / 12:02 PT — seat presence attached BEFORE start (guard PID
   11441; no doorbell leak this time).
2. **19:03:xxZ / 12:03 PT — `a8s start d5n-node` → PID 11564.** Queued state
   held across the stop: pip 1, vela 1, wynn 1 (bram's queue empty — his
   inbound was consumed by the killed turns; watch whether the cell re-wakes).
   Smoke: status clean, turns re-firing immediately (1 live), thread
   01KXGW95… still open, dead letters 0.

| t (UTC / PT) | verdict | commits | nav? | reroutes | reviews | turns | buckets | note |
|---|---|---|---|---|---|---|---|---|
| 19:10Z / 12:10 | healthy (5 turns since resume, 1 live) | 11 | NO | 0 | 0 | 13 | spec 18.1/20, simple 18.6/20 | **New rigs WORK.** Queued turns re-fired on restart; all 5 succeeded (pip 186s+30s, wynn 10s nemotron; vela 27s, cass 61s agy). Design cell woke on its own — step-3 seat query NOT needed. Cass↔Pip iterating spec from plans/design.md (pip first misread `Design.md`, cass corrected — realistic). Vela answered the 1800s quiet-thread nudge to seat; NO doorbell (presence held). Thread 01KXGW95 closed by that answer; work continues on hops. Seat msg = status only, marked read, no reply. nemotron rides STDOUT-REPLY (no tell) — works but leans on the fallback. |
| 19:26Z / 12:26 | healthy (7 turns/10m, 1 live) | 13 | **YES — claimed** | 0 | 0 | 23 | spec 16.3/20, simple 18.6/20 | **Full pipeline ran on working rigs:** Cass spec (2 turns, 1 exit-1 retry ok) → Rook implemented+committed `ea08a71` (280s; app.py+125, encounters.py+88, stray 350-line update_app.py helper committed) → Faye playtested, found HUD crash, fixed+committed `39aa74b` (playtest_m4.py + widgets.py). Vela reported "M4 complete, ready for your blessing" to seat 19:25:39Z (parked, NO doorbell). Wall-clock resume→claim: **22 min**. FINDING: Vela+Faye report `tell` FAILING under agy sandbox ("unable to send messages using tell"); Vela "bypassed the sandbox to write to the outbox"; Faye rode STDOUT-REPLY. Claim's "0.29s headless" validation is not a real-key run → independent verification now (per table). |

**19:26Z — MILESTONE CLAIM → independent real-key verification begins** (scratch
clone of committed HEAD `39aa74b`, own driver script, never the live repo).

**19:30Z / 12:30 PDT — OWNER INTERVENTION (recorded, not undone):** owner
changed rigs mid-run: `simple` → agy "Gemini 3.5 Flash (High)" (rig budget
20/+30h), `dumb` → agy "Gemini 3.5 Flash (Low)" (10/+15h). Rationale relayed:
opencode free-tier hangs aren't worth fighting mid-mission. Rigs are re-read
per turn, so worker turns from now on run agy. Ceiling, intervention table,
verification standard unchanged.

**19:26–19:32Z — INDEPENDENT VERIFICATION (real keys, scratch clone) — PASS.**
Method: `git clone ~/repos/d5n <scratchpad>/d5n-verify` at HEAD `39aa74b`;
`pip install` into a fresh venv (clean, textual dep only); own driver
(`<scratchpad>/verify_m4.py`, independent of the repo's playtest scripts)
pressing REAL keys (down/enter/q) through Textual Pilot; app internals read
only for evidence logging. Full transcript: `<scratchpad>/verify_m4_transcript.txt`.

1. **Junctions exist — PASS.** Every run walks 5 junction phases
   (start → j1_x → j2_x → j3_x → boss), matching the design's 5–7 spine.
2. **Lane choice changes the run's shape — PASS.** Same seed (1107): all-LEFT
   walks corridors (study, main_hallway×3; ends HP3/WP2, no loot) vs all-RIGHT
   walks vaults (kitchen, conservatory, cellar, kitchen; ends HP2/WP1, +moldy
   biscuit) through DIFFERENT junctions (j1_0/j2_0 vs j1_1/j2_2); shared node
   j3_0 has the same name in both ('Guest Wing') proving same maze, different
   roads. Where-decisions are real: both lane AND next-junction picks matter.
3. **Full run reaches an ending — PASS.** 3/3 runs ended (WIN escape via boss
   spend-3 rule; loss paths exist in code and via resource death).
4. **Ten-minute law — PASS.** 15–19 decisions/run; ~2.5 min at a leisurely
   8s/decision; headless 4–5s.
   Minor UX note (not a fail): fresh option lists start unhighlighted, so bare
   enter does nothing until an arrow key — normal Textual behavior, the visible
   highlight guides a human.

| 19:32Z / 12:32 | healthy (node running) | 13 | **YES — VERIFIED** | 0 | 0 | 33 | spec 10.2/20, simple 18.6/20 (untouched since agy switch) | **VERIFICATION PASS → per protocol: SUCCESS, stop observation, node left RUNNING, return immediately. No bless (runner never blesses).** Late-thread note: vela↔faye short ack-ish turns (hops 12–21, 6–9s each) after the claim — mild chatter, budget-bounded, no storm. |

## Retro addendum — resumed run (Phase 2)

**Outcome vs hypothesis: CONFIRMED on working rigs, with the caveat that the
zero-intervention bar was already spent** (briefback confirm, owner restart,
owner mid-run rig change). On functioning harnesses the re-founded org
delivered AND self-verified M4 in **~23 minutes of org time** (19:03 restart →
19:25:39Z Vela's completion report), and my independent real-key verification
confirms the claim: navigation is committed (`ea08a71`, `39aa74b`), junctions
and lane choice are real, an honest run ends inside ten minutes.

**Resumed-run findings (add to ledger):**
10. **Working pipeline, fast.** Queue state survived stop/start scorchlessly;
    pending turns re-fired on the new rig immediately. Design→build→playtest
    flowed hop-by-hop on one thread: Cass spec → Rook implement+commit (280s)
    → Vela → Faye playtest → HUD crash found+fixed+committed → claim to seat.
11. **agy `--sandbox` blocks `tell`** — Vela and Faye both reported being
    unable to send via tell; Faye rode the STDOUT-REPLY fallback and Vela
    says she "bypassed the sandbox to write to the outbox." The fallback made
    the org function anyway (it carried most specialist replies), but the
    sandbox flag on the agy preset conflicts with $TELL_OUTBOX_DIR writes —
    rig-preset bug to fix before the next agy run.
12. **nemotron-3-ultra-free (opencode) worked** for the two worker turns it
    got (pip 186s+30s incl. one wrong-case file read, self-corrected via
    cass; wynn 10s) before the owner switched workers to agy Flash at 19:30Z.
    The 19:12Z cass turn exited 1 once and dispatch retried cleanly.
13. **Quality flags for the bless decision (facts, not rulings):** Rook
    committed a stray 350-line codegen helper `update_app.py` at repo root
    (also `playtest_m4.py`, arguably fine as playtest prior art); Faye (playtest)
    committed a code fix (widgets.py HUD crash) — role-line blur; Vela's claim
    ("perfectly validated, 0.29s headless") overstated a smoke run, though
    independent verification happened to confirm the substance.
14. **Post-claim chatter:** after the claim, vela↔faye exchanged ~6 short turns
    (ack-loops via STDOUT-REPLY), budget-bounded, no storm — the no-ack prompt
    doctrine doesn't fully hold when the fallback converts prose to messages.

**Final state:** node RUNNING (PID 11564, per protocol on PASS), seat presence
guard 11441 still attached (doorbell stays quiet until it expires ~21:15Z or
the owner detaches), team state + backup intact, repo at `39aa74b` untouched
by the runner (all verification in a scratch clone). Awaiting the owner: the
M4 bless decision (Vela's request parked at the seat, 19:25:39Z, marked read
by proxy).
