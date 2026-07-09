# r4t — Router For Teams (v0)

Standalone app: `apps/r4t/`, shim `~/bin/r4t`. a8s wiring via
`apps/r4t/example-definition.json` only. One a8s node per team repo, bound
to a namespace prefix (`a8s namespace s1l <node>`), self-routing on the
`$RECIPIENT` sub-address to roster members.

## Deferred

- **Leader fan-in** — "wake the leader when all N delegated replies land."
  Needs the ledger to record delegation fan-out (which tells a leader turn
  produced, matched by task header) and a completion counter that fires one
  synthetic wake instead of N interleaved ones.
- **Daily/global spend ceilings** — per-team and per-day weighted-turn
  budgets on top of the per-task budget, enforced the same way (park +
  notify), fed from `velocity.csv`.
- **Worktree-per-agent isolation** — run each member's harness in its own
  git worktree instead of the shared root, so concurrent turns can't step
  on each other's working tree; merge/PR discipline becomes the roster's
  problem instead of a scheduling constraint.
- **Roster→config sync command** — `r4t harness sync` proposing harness
  config edits from roster drift (new members, renamed tiers), so the
  out-of-repo file stays the reviewed boundary but is cheap to maintain.

## v0 decisions

| Topic | Decision |
|-------|----------|
| Roster | In-repo `ROSTER.md`, `### Name` blocks + bullet fields; malformed block disables that member only |
| Tiers | Symbolic names in roster; argv + limits only in out-of-repo `~/.r4t/harnesses.json`; unknown tier fails closed |
| Pins | Config `pins` map silently overrides roster Harness per agent |
| Task envelope | `[r4t task=<ulid> hop=<n>]` header; parsed+stripped inbound, given verbatim (hop+1) to agents to copy outbound; missing header → new task, hop 0 |
| Turn budget | Weighted: a turn by tier with `max_turns_per_task=M` costs 1/M of the task's 1.0 budget; exceed → park under `tasks/<id>/parked/` + tell creator once; `r4t task approve` extends |
| Hop limit | Incoming hop ≥ tier `hop_limit` → drop turn, tell task creator once |
| Concurrency | Live PID locks per agent, counted per tier; over limit → park in `pending/` (a8s wake exit codes are only logged — nonzero does NOT redeliver, so local parking replaces requeue) |
| Drain | Parked/pending messages redispatch at the start of every `dispatch` and on `clear` (idle) |
| Memory | `agents/<name>/history.md`, ~8KB, truncated oldest-first at `## ` entry boundaries |
| Acks | None. Prompt says silence is the null outcome; r4t only tells the sender on errors/governance blocks |

## Status

- [x] CLI + governance + tests
- [x] example-definition.json + example-harnesses.json + README
- [ ] Phase 2: leader fan-in (deferred, above)
