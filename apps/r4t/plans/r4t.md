# r4t — Roster For Teams (v1.0, pre-merge)

Standalone app: `apps/r4t/`, shim `~/bin/r4t`. a8s wiring via
`apps/r4t/example-definition.json` only. One a8s node per team repo, bound
to a namespace prefix (`a8s namespace acme <node>`), self-routing on the
`$RECIPIENT` sub-address to roster members.

Design rationale and prior art for everything in the governance stack:
[docs/governance.md](../docs/governance.md).

## v1.0 revision (do before merge)

The branch's current implementation has three flaws found in design review.
Fix them now — nothing below is a compat concern because nothing is merged.

1. **Remove the tell shim (`tellproxy.py`).** Interception moves to
   `$TELL_OUTBOX_DIR`: dispatch points each harness subprocess at a
   per-turn staging outbox; the agent uses the real `tell` unmodified.
   After the turn, dispatch processes staged envelopes — attribution is
   free (only that agent wrote there), the task/hop header is stamped
   into content mechanically (the LLM never sees or copies headers), send
   quota applies, class marking applies — then releases them into the
   node's real outbox for a8s ingest. No shim to keep in sync with `tell`
   as it grows capability. The delegation graph (which asks a leader turn
   produced) falls out of staging for free and feeds the ledger.
2. **Remove every human gate.** `park + tell creator + r4t task approve`
   is replaced by autonomous paths: budget exhaustion wakes the leader
   for a forced-synthesis answer; suppressed/cut messages dead-letter
   with an audit record; deferred (concurrency/cadence) messages drain on
   subsequent dispatch/idle passes. `r4t task approve` is deleted.
   Operator control is config knobs + `r4t status` + dead-letter
   inspection, never a queue that waits.
3. **Actor doctrine, not concurrency, for leaders.** Turns never block
   waiting for replies (`tell --sync` intra-team is the classic failure mode
   reborn). Prompts instruct: delegate, end your turn without answering;
   you will be woken as replies arrive; answer the originator when you
   have enough. Serial execution then interleaves fine. The a8s `batch`
   option on the node definition coalesces reply bursts into fewer
   leader turns.

New governance layers (mechanisms and evidence in docs/governance.md):

- **Class marking** — stamp released envelopes as automated; classify
  inbound from re-broadcaster nodes (config list, e.g. `chatroom`) as
  bulk; bulk-triggered turns post back to that room ≤ once per window.
- **Content-keyed pair suppression** — (sender, recipient, normalized
  content hash) within `suppression_window_seconds` → dead-letter the
  repeats.
- **Reply-privilege bucket** — per agent; suppression events drain 1,
  clean turns earn `bucket_earn_ratio` (0.1); below half, turns don't
  run (messages still recorded to history); recovers autonomously.
- **Forced synthesis on budget exhaustion** — replaces parking.
- **Governed recovery** — idle nudges capped per agent per period; past
  the cap the leader closes the task with what exists.
- **Deliberate-decision rule** — a human-origin message in a chain
  resets its task budgets.

## Deferred

- **Worktree-per-agent isolation** — run each member's harness in its own
  git worktree instead of the shared root, so concurrent turns can't step
  on each other's working tree; merge/PR discipline becomes the roster's
  problem instead of a scheduling constraint. Next unit of work after
  v1.0 is verified end-to-end.
- **Detached execution mode** — dispatch currently runs harnesses inline,
  so the a8s one-wake-at-a-time model makes teams strictly serial and
  `max_concurrent` never binds above 1. Add background execution with
  lock-counted gating only when serial throughput demonstrably hurts.
- **Daily/global spend ceilings** — per-team and per-day weighted-turn
  budgets on top of the per-task budget, fed from `velocity.csv`.
- **Roster→config sync command** — `r4t harness sync` proposing harness
  config edits from roster drift (new members, renamed tiers), so the
  out-of-repo file stays the reviewed boundary but is cheap to maintain.
- **Leader fan-in wake** — "wake the leader once when all N delegated
  replies land" instead of N interleaved wakes. The staging-outbox
  delegation graph provides the bookkeeping; a8s `batch` plus the idle
  nudge may make this unnecessary — revisit with usage data.

## v1.0 decisions

| Topic | Decision |
|-------|----------|
| Roster | In-repo `ROSTER.md`, `### Name` blocks + bullet fields; malformed block disables that member only |
| Tiers | Symbolic names in roster; argv + limits only in out-of-repo `~/.r4t/harnesses.json`; unknown tier fails closed |
| Pins | Config `pins` map silently overrides roster Harness per agent |
| Send interception | Per-turn `$TELL_OUTBOX_DIR` staging; real `tell`; post-turn release with attribution, header stamp, quota, class mark. Release is post-turn only — no mid-turn sweep (inline execution is serial anyway; revisit with detached execution) |
| Intra-team release | Staged envelopes addressed `<node>` / `<node>:*` bypass the real outbox (a8s drops self-sends) and go straight to the team `pending/` queue; drained until quiet around every dispatch |
| Task envelope | `[r4t task=<ulid> hop=<n> auto]` header stamped/stripped by r4t only; `auto` is the message-class mark; missing header from external senders → new task; a header WITHOUT `auto` is a deliberate decision → task budget reset |
| Turn budget | Weighted per tier; exhaustion → one forced-synthesis leader turn, then the task is closed; later messages to a closed task dead-letter |
| Bucket recovery | Violations at release drain 1.0; clean turns earn `bucket_earn_ratio`; below half, inbound records to history only AND earns the ratio, so a muted agent recovers autonomously as traffic arrives |
| Idle invoke | Node definitions call `r4t idle` without `--node` (sole-team default); the a8s `$RECIPIENT` there is the agent name, not the namespace prefix, so it must not be passed as the team |
| Hop limit | Incoming hop ≥ tier `hop_limit` → cut, dead-letter, originator informed once |
| Concurrency | Live PID locks; inline execution means serial today; deferred messages drain on later dispatch/idle passes (a8s does not redeliver on wake failure — verified: messages are trashed at wake time) |
| Memory | `agents/<name>/history.md`, conversation only (messages in/out), never raw stdout; ~8KB, truncated oldest-first |
| Acks | None. Prompt says silence is the null outcome; enforcement is suppression + buckets, not etiquette |
| Observability | Traffic via a8s txlog/convo; decisions via dispatch stdout → a8s node log; state via `r4t status` + dead-letter dir |

## Status

- [x] CLI + tests (107) on branch
- [x] v1.0 revision above (tellproxy + approve gone; staging release,
      class marking, pair suppression, buckets, forced synthesis, nudge
      cap, deliberate-decision reset; `r4t init`; `R4T_HOME`; sandbox
      with `--fake` e2e — 147 tests green)
- [x] README (regenerated — knob table links docs/governance.md)
- [ ] End-to-end: register a team, `tell acme:gerry`, watch a real
      delegate/synthesize cycle under throttle
