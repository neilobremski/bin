# Sub-dividing rosters — the options, in plain English

*Design exploration, 2026-07-11. Nothing here is decided. Read when
rested; each scenario is a separate short doc.*

## The problem in three sentences

A 21-member team with one leader melted into noise: the leader spent his
whole budget routing instead of thinking, and the human watching couldn't
tell work from churn. Human organizations solved this centuries ago with
**span of control**: nobody manages more than a handful of people
directly, so companies grow by adding *layers* (teams, departments), not
by widening one room. The question is how r4t should grow layers.

## The four scenarios

| | Shape | New machinery | Storm containment | Cost |
|---|---|---|---|---|
| **A. Team labels** | one roster, `Team:` tags | tiny | weak (advisory) | near zero |
| **B. Nested gardens** | each department = its own roster + node | none (composes today) | strongest (walls) | more nodes to run |
| **C. Chain of command** | one roster, enforced reporting tree | medium (dispatch rule) | strong (topology) | one node, new rule |
| **D. Dynamic squads** | leader forms temporary squads per job | most | good | leader does org design |

- [a-team-labels.md](a-team-labels.md) — cheapest, weakest
- [b-nested-gardens.md](b-nested-gardens.md) — uses what's already built
- [c-chain-of-command.md](c-chain-of-command.md) — structure without extra nodes
- [d-dynamic-squads.md](d-dynamic-squads.md) — the org chart is a bench

They are not mutually exclusive. A realistic v1 is probably **C inside a
garden, B between gardens** — a reporting tree within each team, and
whole teams appearing as single members of bigger teams.

## The orthogonal decision (kept separate on purpose)

Independent of *shape*, there is the economics question from the CTO
discussion: today budgets attach to **conversations** (a thread gets ~25
turns, then it is force-closed; messages past a hop count are cut).
The alternative attaches budgets to **members** (each agent has capacity
per hour; exhaustion means *work waits*, never *message dies* — throttle,
not block). Every scenario below works under either economy, but the
member-capacity model removes the two moments that felt like betrayal in
practice: a work product cut mid-flight, and a meter filling while the
human sits still. Whichever shape wins, I recommend pairing it with
member-capacity budgets.

## What stays the same in every scenario

The parts that already earn their keep survive untouched: the seat (you
as a first-class roster member), chat/status/logs observability, the
repeat suppressor, the failure breaker, sender attribution by
filesystem, and the rule that the originator always eventually gets an
answer. Nothing in these docs discards them.
