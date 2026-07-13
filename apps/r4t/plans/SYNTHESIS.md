# R4T re-founding — synthesis and attack plan

*2026-07-12. Synthesizes the CTO discussion (2026-07-11), the four
department scenarios (plans/departments/), and Neil's voice memo taken
on his run after listening to the audio version. This is the working
plan; individual pieces still go through issues + PRs as usual.*

## The decided direction (from the memo)

All four scenarios are destinations, not alternatives. The sequencing:

- **v1 = C inside B**: a reporting tree *within* each walled garden,
  and gardens composing as single members of bigger gardens (the
  ingress/egress abstraction already built). Squads (D) are **v2** —
  great for skunkworks and targeted fixes, wrong for autonomous
  project development.
- **The topmost leader IS the garden.** From outside, messaging the
  garden means messaging its leader. Neil talks to Gerry; Gerry talks
  to everyone else. Structurally enforced: members cannot message
  outside their team — they go through their lead.
- **Leads are intelligent switchboards.** Put the frontier models at
  the lead level: high intelligence, low tool use, small context.
  Workers can run on cheap/local models.

## The economics (firmly decided)

**Per-member budgets. No message cutting. Ever.**

- Exhausted budget means the agent *is not allowed to run* — messages
  queue, nothing is lost. Work waits; messages never die.
- Bifurcated quotas, mirroring real AI plans (5-hour window + weekly):
  each member has an individual quota, and the team has a shared quota.
  An expensive frontier-model team gets a low quota and runs slowly and
  smartly; a local-model team (ollama/Qwen on idle CPU) runs almost
  freely. Throttling controls *money*; concurrency controls *CPU*.
- Design goal: plug in underused subscriptions (Google AI Pro via agy,
  local ollama) so "nothing goes to waste" — a slow furnace of work,
  always warm, never blowing a budget.

## Batch invoke — the storm suppressor

Today each inbound message triggers its own turn. The memo's insight:
when an agent wakes (budget replenished, idle cadence, whatever), it
should receive **all waiting messages at once** in a single prompt. An
agent that sees "teammates discussed X, then the lead overrode with Y"
in one reading pivots nimbly instead of burning three turns reacting to
each message in sequence. a8s definitions already model a batch invoke
endpoint; r4t dispatch should drain the member's queue into one turn.
This is both a quota saver and a storm damper.

## Milestones, not tasks

The task-with-turn-budget model gets demoted. The hierarchy of intent:

    mission          (why the project exists — S1L has one)
    └─ milestone     (next demo / release — "what we're driving at")
       └─ sprint     (a lead's decomposition)
          └─ activity (turns, messages — the churn)

- The human messages the top of the pyramid with the *current
  milestone*; leads decompose downward.
- The machinery idles but never stops: leads have a shorter idle-invoke
  cadence than workers (Gerry is the spark that ignites the tree; a
  lead developer periodically reviews the team's code unprompted).
- "We're not trying for perfect, we're trying for done." Teams drive at
  the milestone; the human is the one who rules a bug ship-blocking vs
  deferred.
- A calendar/cadence node can ping the lead ("how's it going?") and
  relay the answer to the human.

## Walk-the-factory-floor access

Everything routes through leads *except* the human's inspection right:
the boss can always ask any individual "what are you doing? how's it
going?" Mechanism sketch: a temporary pass — one reply, or a time-boxed
window — that lets that one agent answer outward across the wall
without making cross-wall messaging generally available.

## Observability aggregates up the tree

Turn counts, budget burn, and health roll up: platform-team status
includes its child engineering garden's activity. `r4t status` at the
root shows the whole pyramid; drill down per garden.

## Simplifications accepted for v1

- **Single repo, no git worktrees.** Teams may map to working folders
  within the repo (art touches `css/`/`palettes/`, testers read a
  shared `dist/`), but folder enforcement is soft; if agents stomp each
  other, the org design is wrong.
- One handful of ROSTER.md files (an org map), not config sprawl.
- Naming: we need a short, greppable term for a nested garden (like
  "rig" was for runners) — people/place flavored. Candidates to riff:
  crew, cell, wing, desk, floor, guild, pod. **Not decided.**

## Immediate work (this week)

1. **Rig swap** — bench Cursor (`agent` CLI) in the live s1l/ttt config
   while its quota recovers; `agy` (Antigravity, underused Google AI
   Pro quota) takes the lead/smart seats; opencode + ollama keep
   developer/simple seats. Cursor stays a first-class preset — and
   `r4t rig swap <rig> <preset>` makes flipping back a one-liner that
   preserves the rig's other settings.
2. **`tells` CLI** — remove `tell --sync`; new `tells` waits for ANY
   message to the node within `--timeout` (default 5s), like
   `a8s drain`'s shape. Simpler contract: no session handshake, no
   expected-sender matching.
3. **Member budgets + no-cut delivery** in r4t: replace task turn
   budgets with per-member capacity; exhausted member's turns defer
   (messages queue), never dead-letter. Leader messaging a broke agent
   just queues it (async — no bounce needed).
4. **Batch invoke** in dispatch: one turn consumes the whole queue.

## Next after that (ordered)

5. Enforced reporting tree within a garden (topology replaces hop
   limits — a tree cannot loop).
6. Garden-as-member composition hardening + the naming decision.
7. Milestone/mission ground-truth doc the leads re-read every turn
   (fixes the stale-ground-truth failure from the S1L postmortem).
8. Aggregated status up the tree; idle-invoke cadences per role.
9. Walk-the-floor pass.
10. Squads (v2). Calendar node. Marketing/sales/playtest roles.
