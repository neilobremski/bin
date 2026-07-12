# Cell re-founding — implementation spec

*2026-07-12. Decisions locked with Neil: member budgets (no mutes),
task demotes to thread label with answer-the-originator, hard tree
enforcement, and the nested-garden unit is named the **cell**.
This spec covers the phased build on the working branch. Direction
rationale: [SYNTHESIS.md](SYNTHESIS.md).*

## Phase 1 — member queue, budgets, batch invoke

The keystone: budgets, no-message-cutting, and batch invoke are one
data structure — a durable per-member queue.

### Queue

- `~/.config/r4t/teams/<node>/agents/<member>/queue/` — one JSON
  envelope per file, ULID-named (arrival order). Every inbound message
  to a member **enqueues, unconditionally**. No gate ever drops or
  dead-letters a deliverable message again.
- Duplicate collapse replaces pair-repeat suppression: when enqueuing,
  if the newest queued entry has the same sender and identical
  normalized content, collapse (keep one, bump a `repeats` count).
  Collapsing loses no information; it is the only "suppression" left.
- Dead letters remain ONLY for undeliverable mail (unknown recipient,
  malformed) — telemetry, not policy.

### Budgets (replace mutes AND task turn-budgets)

- Rework the token bucket: `budget_max` / `budget_earn_per_hour` per
  member (rig-level defaults, per-member override later). A turn costs
  1 unit regardless of how many messages it consumes — batching is
  rewarded by construction.
- Team bucket: node-level `team_budget_max` / earn rate; a turn also
  costs 1 team unit. Both must be ≥1 for a member to be runnable.
- Empty bucket = **resting**: the member simply isn't runnable; the
  queue holds. Nothing is recorded to history as skipped, nothing is
  muted, no unmute threshold. Status language: "resting (N queued,
  ready in ~M min)".
- Deliberate human/seat messages still run the recipient synchronously
  (seat send path) but still charge the buckets; if the bucket is
  empty the seat send reports "queued — Gerry is resting" instead of
  running. (The human is never blocked from *sending*.)

### Turn shape (batch invoke)

- The dispatcher picks a runnable member with a non-empty queue
  (existing throttle: max_concurrent, cadence between turn starts,
  breaker all still gate runnability — unchanged).
- ONE turn drains the ENTIRE queue at pick time: the prompt renders all
  queued messages chronologically under a "Messages since your last
  turn" section (sender, thread label, body; collapsed repeats noted
  as "(sent N times)"). Messages arriving mid-turn stay queued for the
  next turn.
- Replies/stdout-fallback/staging/egress: unchanged mechanics, but a
  reply is attributed to the thread of the message it answers (the
  agent's tell carries the thread header exactly as today's task
  header).

### What phase 1 deletes (scorch-the-earth, no shims)

- Mute machinery: mute records, unmute threshold, bucket-muted dead
  letters, muted-member verdicts, forced-synthesis bypass for muted
  leaders.
- Task turn budgets: `used`/`budget` fractions, `max_turns_per_task`,
  forced leader synthesis at 100%, budget-exhausted dead letters.
- Hop-cut message dropping: hop counts stay STAMPED (telemetry +
  Phase 2 tree work) but never cut a message.
- Per-message-triggered turns: dispatch consumes queues, not single
  envelopes.

### What phase 1 keeps

- Task id as **thread label** (rename in prose/UI to "thread"; the
  header stays `[r4t task=...]` on the wire for now — wire rename can
  ride Phase 2).
- **Answer-the-originator**: a thread opened by the human closes when
  the human gets a substantive reply — and interim replies are fine;
  the originator can be answered many times, closure just needs one.
- Quiet sweep: a thread quiet past `quiet_task_seconds` with an
  unanswered originator still triggers the leader wake — as a nudge to
  reply with current state, not to force-finish the work.
- Breaker, throttle cadence, egress/ingress headers, seat model,
  stdout fallback, sender attribution.

### Surfaces to update

- `verdict.py`: replace muted/budget-hot verdicts with resting
  ("Gerry resting — 3 queued, ready ~14:20"), team-bucket verdicts,
  queue-depth warnings (e.g. >10 queued on one member).
- `r4t status`: budgets panel (member bars = bucket fill, not task
  burn), queue depths.
- chat TUI header: budget bars now show member buckets for the
  members you're talking to; open-thread count replaces open-task
  count.
- README + tutorial: budgets/threads sections rewritten; governance
  knob table updated (knobs removed AND added).
- Tests: full rewrite of budget/mute/task-budget tests; new queue
  tests (enqueue-always, collapse, batch drain, resting, team bucket,
  seat-send-while-resting). All existing passing tests either survive
  or are consciously deleted with the machinery they tested.

## Phase 2 — the tree (separate commit series)

- `ROSTER.md` gains `Cell:` and `Lead:` lines per member; whole org
  stays in ONE ROSTER.md (Neil's call — agents may see it; harmless).
- Hard enforcement: a tell to anyone other than your lead, your
  direct reports, or your own cell-mates reroutes to your lead with a
  note ("forwarded: Phil tried to reach Marketing"). The human seat is
  exempt (walk-the-factory-floor comes later, but the seat can always
  reach anyone).
- Hop limits retire inside a cell/tree (a tree cannot loop).

## Phase 3 — mission/milestone doc

Decided 2026-07-12 (nested intent, per commander's-intent doctrine):

- `MISSION.md` at the repo root, HUMAN-owned, ≤1 page: purpose + end
  state + current milestone, never the how. It outranks every other
  document in the repo. Changes only at milestone boundaries, and a
  change triggers the briefback ritual: the top lead restates the
  intent in their own words to the human before work resumes.
- **Injection is leads-only.** Members with reports get MISSION.md in
  every turn prompt; ICs receive their portion as ordinary messages
  from their lead, restated at the resolution the receiver can hold
  (a dumb-rig member's whole world is its lead's message — by design).
  Any member with tools may read the file directly; no machinery for
  that.
- Intent flows edge-by-edge down the tree, restated at every hop —
  "who gets the mission" has the same answer as "who reports to whom".
- Deliberately NOT building: per-rig `mission:` knobs, mechanized
  milestone tracking (status fields, staleness verdicts). Milestones
  stay prose; the human interprets them. Revisit only if d5n shows
  specialist drift.
