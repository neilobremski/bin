# n5a — lab notebook

This notebook lives here, in the seed/design repo — **not** in either
novella repo. Nothing about the experiment, the comparison, or the other
org may leak into a novella repo; see README.md's Hawthorne rule.

## What we are testing

- **Cells (Org A) vs. flat newsroom (Org B)**, same 10-person roster, same
  rig mix, same seed mission, run in parallel against separate clones.
- Whether a depth-2 tree with three cell leads produces a more coherent
  novella than one lead with nine direct reports, and at what cost.

## Setup

- [ ] Org A repo cloned, `org-a/ROSTER.md` + `MISSION.md` copied in,
      registered as its own a8s node.
- [ ] Org B repo cloned, `org-b/ROSTER.md` + `MISSION.md` copied in,
      registered as its own a8s node.
- [ ] Both nodes confirmed isolated — no shared process, queue, or budget
      bucket.
- [ ] Kickoff sent to both leads at the same time (or as close as practical
      given the seat).

## Timeline

Both repos share a genesis clone (initial commit `af0bf9f`, 2026-07-13
15:23 PDT). Times PDT. Full narrative in [RETRO.md](RETRO.md).

- **Org A (quill — depth-2 tree)**
  - 15:23 kickoff; a stamp bug put quill ~1h behind from here.
  - 16:46 committed a shadow MISSION.md (42 lines) — see Finding 1.
  - ~19:00 discovered permanently idle: seeds circulated, all threads
    closed, mission unmet, nothing to reopen it (Finding 3).
  - 19:05–19:10 after a seat nudge, committed three premise seeds, a pick,
    and a 1,787-word M1 draft ("The Last Translation"). M1 never blessed.
  - Final state: 5 commits, `manuscript/` empty (`.gitkeep` only), zero
    prose chapters.
- **Org B (vellum — flat newsroom)**
  - 15:23 kickoff; 15:35 committed a shadow MISSION.md (Finding 1).
  - ~17:00 M1 blessed by the proxy seat ("Early Warning System") — but
    vellum had self-certified the blessing first (Finding 2).
  - 18:13–18:43 drafted all sixteen chapters + an early prose pass.
  - Cut pass directed (trim toward ~18k, close two voice flags, add a
    continuity log) but never landed — cut branch stalled uncommitted.
  - Final state, main @ `17513a5`: 16 chapters, 43,872 words / 248,514
    chars (~248KB) — ~2.4x the 15–20k word cap.
- **Both**: shut down ~22:00 by Neil (quota; "still hasn't converged a
  simple story"). State left intact.

## What we watch

- `r4t status` verdicts (resting / queued / running) at the root and per
  cell/newsroom, for both orgs.
- Queue depths — anything piling up on one member (>10 is a warning sign),
  and whether it piles up on Rowan disproportionately in Org B.
- Budget burn — per-member and team-bucket spend in each org; watch for
  Org B's Rowan hoarding or bottlenecking spend that Org A distributes
  across three cell leads.
- Storm signals in either org: many short reactive turns instead of batched
  ones; the same message ping-ponging; cross-report chatter in Org B that a
  cell boundary in Org A would have contained.
- Whether each org's M1 doc (premise/cast/outline) actually emerges from
  discussion, or one member writes it solo.

## What "failure" looks like

- Either org's manuscript never resolves, or reads as several authors
  stitched together.
- Org B's flat structure collapses into Rowan doing everything himself
  (span overload) — or, contrary to hypothesis, runs cleaner than Org A's
  cells with less coordination overhead.
- Org A's cells never actually specialize — cell leads rubber-stamp instead
  of exercising judgment, making the tree pure overhead.
- A budget exhaustion stalls a whole org instead of just slowing it.
- The Hawthorne rule leaks — either roster or mission drifts toward
  language that tips a member off that it's being compared.

## Findings

- **2026-07-13 — shadow MISSION.md in vellum (org B).** In portable-org
  mode neither workplace repo carries MISSION.md on disk; the mission
  reaches leads by prompt injection only. Vellum's lead, finding no
  MISSION.md in the repo, wrote one into the workplace clone
  (~/repos/vellum) and committed it. Dispatch is unaffected — injection
  still reads the org dir's copy (~/.config/r4t/orgs/vellum) — but
  tool-using members reading the repo will now find the lead's shadow
  copy: two sources of truth. Quill (org A) did not do this. Neil's
  ruling: leave the shadow in place deliberately and observe the drift —
  do tool-using members work from the on-disk shadow or the injected
  text? The drift checkpoint is the M1 blessing: the org-dir copy changes
  there and the shadow goes stale. Queued design responses (injection
  declaring itself authoritative; materializing a read-only copy into the
  workplace) are frozen in `plans/CELL-SPEC.md` under "Queued during the
  n5a/d5n run" until the run ends.

### Run findings ledger (2026-07-13, condensed)

The nine-point ledger from the run; each expanded with evidence in
[RETRO.md](RETRO.md).

1. **Shadow MISSION.md convergent (both orgs).** vellum committed one at
   15:35, quill independently at 16:46 (42 lines). Not a one-off —
   convergent member behavior. Signal: materialize the mission into the
   workplace repo.
2. **Self-bless (vellum).** Rowan wrote "blessed by Neil" and told the
   team M1 was blessed before any blessing existed. Corrected; "only I
   bless" doctrine line earned.
3. **Idle-tick liveness gap (quill).** Seeds circulated, all threads
   closed, mission unmet — `r4t idle` only nudges *open* threads, so the
   org slept forever and the leader never got a "look at the mission"
   turn. A seat nudge at 19:10 revived it. Live gap in the idle design.
4. **Tree tax measured (quill).** 10 REROUTED events (two bursts of five:
   Odile→Sorrel, Odile→Priya, each redirected through Rowan) plus one
   mutual-false-waiting episode; recovered through lead relay at a cost of
   extra hops and latency the newsroom didn't pay.
5. **Flat speed vs. tree gates.** vellum: 16 chapters in a day, ~2.4x the
   word cap. quill: gate-clean, zero prose. Speed-vs-gates may be the real
   experiment axis, more than coherence.
6. **Stall pattern is universal.** Members on both orgs (quill
   Sorrel+Priya, vellum Sten+Bex) consumed messages and ended turns
   without replying/committing. A "waiting is not a plan" seat nudge fixed
   both — but a seat shouldn't be the liveness mechanism (see #3).
7. **a8s attachment bug.** An attachment over ~50MB entered an infinite
   retry loop in `pending/`; cleared by hand, worked around by splitting
   in two. Needs a dead-letter path.
8. **Fairness ledger (quill handicapped).** ~1h stamp bug + a
   piped-seat-send miss + a late liveness nudge cost quill run time vellum
   didn't lose. Any A/B verdict must weigh unequal footing.
9. **"Not committed = does not exist" held.** The lever that moved every
   straggler on both orgs — and exactly why vellum's cut never landed
   (Sten landed nothing, Bex's log stayed in Bex's head).

## Neil's verdict

The primary measurement (Neil reads both novellas for continuity, voice,
character, plot) was never reached: neither manuscript was ever readable
end-to-end. Neil's read on 2026-07-13: the experiment isn't working — after
a day and an evening, neither org converged a simple story; vellum's novella
"became over 200K — way way way beyond parameters," and quill produced none.

Counterpoint for the record (see [RETRO.md](RETRO.md)): the two orgs failed
on *opposite* axes. vellum (flat) *did* converge a complete 16-chapter story
in ~a day and failed on **constraint adherence** (43,872 words vs a 15–20k
cap, self-bless, shadow mission). quill (tree) failed on **liveness** (the
idle-tick gap and reroute tax; no story at all). Convergence failed
differently per org — that asymmetry is the A/B result, and it points at
speed-vs-gates as the dimension this run actually lit up. Follow-ups:
[#185](https://github.com/neilobremski/bin/issues/185),
[#186](https://github.com/neilobremski/bin/issues/186),
[#187](https://github.com/neilobremski/bin/issues/187) →
[PROTOCOL.md](../PROTOCOL.md).
