# ORG-LESSONS — what human org research says about r4t's finishing touches

*Synthesized 2026-07-12 from five deep-research runs (raw/q1–q5). Sources
named inline; full citations in raw/.*

## Executive summary

1. r4t's core bets are all evidence-backed: small cells, hard tree, short
   intent doc, throttling budgets, batch turns. Nothing needs reversal.
2. Team-size research converges hard: ~4–6 for tight collaboration
   (Hackman's ideal ≈4.6), diminishing returns past 8, fragmentation past 10.
3. Depth is the bigger danger than width: each layer adds telephone-game
   distortion and latency; keep the tree to 2–3 levels until forced deeper.
4. Strict chains reliably filter bad news upward (Challenger, Deepwater
   Horizon); a hierarchy needs at least one built-in skip-level channel.
5. Commander's-intent doctrine validates MISSION.md exactly: purpose + end
   state + constraints, never the "how"; add a briefback/confirmation loop.
6. Throttles resist gaming because they have no sweet spot to hit — capacity
   caps make cheating a null move, unlike quotas (NHS, CompStat evidence).
7. Prose-only milestones match Jensen's cure for budget gaming: sever the
   reward-target link; judge accomplishment, don't count a proxy.
8. Batch-reading beats interrupts empirically (Mark 2016, Kushlev & Dunn
   2015: ~3 checks/day lowered stress, raised rated productivity).
9. Cadence should differ by role: leads wake often (manager schedule),
   workers wake rarely with long focus blocks (maker schedule, Graham 2009).
10. Over-batching needs guardrails: urgency escalation, response SLAs, and a
    timeout/default rule so two sleeping agents can't deadlock.

## Cell size & tree depth

**Evidence.** Hackman's research puts the collaboration sweet spot at ~4–6
(ideal ≈4.6); Amazon two-pizza caps at ~5–10; US Army fire teams are 4–5,
squads 8–12, platoons ~30; Dunbar's innermost layer is ~5. Pairwise links
grow combinatorially (6 people = 15 links, 10 = 45), which is why >10
fragments. Industry EM:IC ratios settled at 1:6–9 (Jellyfish 2025 survey of
Google/Microsoft/Amazon; Microsoft found defect rates rise past 10 reports).
On depth: TeamChart benchmarks 3–4 layers for a 50–200-person org, each
layer costing ~10–15% overhead plus decision latency; collapse layers that
only rubber-stamp. Google found senior autonomous teams can run spans of ~15.

**r4t today.** Cells with one lead composing into a tree — the right shape.

**Finishing touches.**
- Default cell size 4–6 members + lead; soft warning at 8, hard cap 10.
- Default tree depth 2 (root lead → cell leads → workers); treat depth 4 as
  a design smell requiring justification, mirroring the 3–4 layer benchmark.
- Since agent leads tolerate wider spans than humans (communication is cheap,
  attention is the constraint), allow a lead's span to widen to ~10–15 only
  when its members are mature/low-touch — and watch the lead's own token
  spend on coordination as the split signal.

## Chain-of-command relief valves

**Evidence.** Strict chains fail in two documented ways: information silos
(Deepwater Horizon post-mortem: engineering/ops split, risk data never
crossed the boundary) and bad-news filtering (Rogers Commission on
Challenger: management contained problems rather than communicating them
forward). Team Topologies (Skelton/Pais) says the fix is not more chatter
but *explicit interaction modes* — X-as-a-Service (formal team APIs) for
routine cross-team needs, collaboration mode only temporarily. Relief valves
ranked essential by the evidence: defined cross-cell channels, periodic
skip-level checks (Friday.app/Thomas: skip-levels measurably improve
unfiltered flow); optional/contextual: tiger teams (Apollo 13 — crisis only),
gemba walks (Deming: useless without specific questions), liaison roles.

**r4t today.** Hard tree enforcement; the human talks to the top lead.
Observability work (#169) is effectively the human's gemba walk — reading
the event log directly rather than trusting rollups.

**Finishing touches.**
- Add a skip-level mechanism: let the human (and optionally the root lead)
  read any cell's raw log/chat directly — read-only piercing, which relieves
  filtering without breaking the write-path chain of command. This is the
  one *essential* valve; ship it before any others.
- Define escalation triggers, not ad-hoc bypass: a member may flag a message
  URGENT to its lead, and a lead must forward (not summarize away) anything
  flagged twice. Guards against Challenger-style containment.
- Keep tiger teams out of v1; they're heavyweight and crisis-only. A future
  "temporary cell" primitive covers it if ever needed.

## MISSION.md intent-doc contract

**Evidence.** Military mission command (ADP 6-0): intent is "purpose of the
operation and the desired end state," explicitly NOT a plan; Mattis — intent
demands more discipline than voluminous instructions. Confirmation briefs /
backbriefs close the loop: the subordinate restates intent and how they'll
act, surfacing misreads before work starts. Shape Up (Basecamp) adds
appetite (a fixed budget you shape work to, not an estimate) and explicit
no-gos. Amazon working-backwards caps the PR at ~1 page. OKR cascade
research (Mind the Product, PerformSpark): mechanically cascaded goals
create wait-states and ignore ground truth — publish themes, let teams set
their own goals. Failure modes: stale intent, over-specification (a hidden
micromanagement order), and vagueness.

**r4t today.** Short human-owned MISSION.md (mission + current milestone),
re-read every turn. Matches doctrine almost exactly.

**Finishing touches.**
- Fix the contract at ~1 page max with four slots: purpose/end-state,
  current milestone (prose), out-of-scope/no-gos, appetite (rough budget or
  deadline for the milestone). The no-gos slot is the piece most often
  missing and cheapest to add (Shape Up's rabbit-hole list).
- Add a briefback: when MISSION.md changes, the root lead's next turn must
  restate the intent in its own words to the human — one message, human can
  correct. Cheapest possible misalignment detector.
- Change cadence: only at milestone boundaries or genuine mission shifts;
  mid-milestone edits should be rare and announced (stale intent is a
  failure mode, but thrash is a worse one).

## Budget / throttle design (Goodhart resistance)

**Evidence.** Outcome targets get gamed everywhere they're tried: NHS 4-hour
A&E target (ambulances held outside, patients parked in hall beds —
Bevan & Hood 2006), CompStat crime downgrading (55% of surveyed retired
NYPD knew of reclassification), call-center AHT gaming. Flow constraints
resist because they have no sweet spot: kanban WIP limits make hidden work
pile up visibly (Atlassian), rate throttles slow you without rewarding
fakery (Verizon's congestion throttling). Jensen (HBS 2001): the cure for
budget gaming is severing the pay–target link — reward genuine
accomplishment, judged, not counted. Becker: rare-but-consequential audits
maintain deterrence without full surveillance. Pair any efficiency metric
with a quality check so gaming one shows in the other.

**r4t today.** Per-member + per-team token buckets that throttle and never
drop; milestones deliberately prose-only after mechanized metrics got gamed.
This is precisely the flow-not-outcome design the evidence endorses.

**Finishing touches.**
- Never surface budget state as a target ("you have N turns left" caused
  premature closing). Express throttle as pure back-pressure: the agent
  just waits longer; the number stays out of the prompt or is coarse
  ("plenty / low"), never a countdown.
- Keep milestone judgment human (prose verdicts are Jensen's severed link).
  If any scoring is ever automated, pair it with a second uncorrelated
  check and random spot-audits rather than making it authoritative.
- Watch for the one throttle-gaming vector that remains: budget-shifting
  (leads hoarding or offloading spend to members). Team-level buckets
  already bound this; just make per-member spend visible in observability
  so hoarding shows up in the log.

## Batch invoke & wake cadence by role

**Evidence.** Mark et al. (CHI 2008): interruptions speed completion
slightly but sharply raise stress/effort; Mark et al. (CHI 2016): batching
email correlates with higher rated productivity; Kushlev & Dunn (2015): a
hard limit of 3 checks/day lowered stress in a controlled trial. Graham
(2009): makers need multi-hour uninterrupted blocks, managers run on hourly
slots — mixing them is the failure. GitLab handbook: bias async, "could this
be done without a meeting"; sync time is only for decisions and unblocking
(Rework guide). Basecamp: written daily/weekly check-ins instead of
standups; 6-week cycles with kickoff and heartbeat. Over-batching pitfalls:
stale context, response-time ambiguity, and mutual-wait deadlock — mitigated
by explicit SLAs (e.g. DM ≈4h, mail ≈24h), handoff notes (done/next/
blocked), and timeout defaults (Apache's vote-when-consensus-stalls).

**r4t today.** Wake → read whole queue → one batch turn → sleep. This is the
empirically favored model, not a compromise.

**Finishing touches.**
- Split cadence by role: leads on a short idle cadence (the manager
  schedule — wake on every member message or a few-minute idle tick);
  workers on a long one (wake on direct task assignment or a much sparser
  tick), preserving maker blocks. Human-scaled analogue: leads 4–6
  wakes/day-equivalent, workers ~3.
- Add an URGENT flag that wakes the recipient immediately — the single
  escape hatch that makes long worker cadences safe (matches the Rework
  SLA pattern).
- Add a deadlock breaker: if a message sits unread past a per-role timeout
  (e.g. one full worker cadence), the sender's lead gets a nudge event.
  Cheap, and it converts silent stalls into visible ones.
- Encourage low-context messages: a turn's outgoing messages should carry
  done/next/blocked state so the reader's batch turn doesn't need archaeology
  (GitLab low-context norm, Rework handoff docs).

## We looked, rejected

- **Daily standup analogue** — Stray et al. 2020: poorly run standups add
  little; async written check-ins dominate. Don't add a sync-all event.
- **Mechanically cascaded OKRs** — creates wait-states and ignores ground
  truth (PerformSpark, Mind the Product). MISSION.md themes + cell-set goals
  only.
- **No-manager flat structures** (Valve/37signals style) — founder at scale;
  one-hub coordination storms. The tree stays.
- **Rigid universal team-size rule** — two-pizza as dogma fragments work;
  size by coupling and lead maturity within the 4–10 band instead.
- **Turn/message quotas as performance targets** — the original gamed-metric
  failure; NHS/CompStat evidence says any countdown target re-creates it.
- **Free-form cross-cell chat as the relief valve** — Team Topologies:
  ad-hoc chatter raises cognitive load; use defined channels + read-only
  skip-level instead.
- **MBWA-style unstructured monitoring** — Deming: walking around without
  specific questions is theater; observability queries should be pointed.
- **Tiger teams as a v1 primitive** — crisis-only, heavyweight (Apollo 13);
  revisit only if a real cross-cell crisis pattern emerges.
