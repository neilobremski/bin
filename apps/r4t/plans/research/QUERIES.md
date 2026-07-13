# Deep-research queries — org structures, PMOs, working methods

*2026-07-12. Five briefs for `n0b ai research` (o4-mini-deep-research +
web search). Each is keyed to a live r4t design decision. Raw outputs
land in raw/; the synthesis in ORG-LESSONS.md.*

## Q1 — span-of-control (→ cell size + tree depth defaults)

I'm designing the governance for an organization of autonomous AI
agents structured like a human company: small teams ("cells") with a
lead, composing into a tree. I need the hard-earned human lessons on
SPAN OF CONTROL and organizational layering. Research: (1)
evidence-backed span-of-control ranges — military fire team/squad/
platoon sizing, Amazon two-pizza teams, Hackman's team-size research,
Dunbar's layers; (2) when organizations add a management layer versus
widen a team, and the observed failure modes of each (too flat:
coordination storms and one-hub decision bottlenecks; too deep:
telephone-game distortion and latency); (3) which ratios/heuristics
survived contact with reality in tech companies (EM:IC ratios) and
which were cargo-culted. Deliver: concrete numbers with sources, the
failure mode each guards against, and a short "what to steal" list for
a machine organization where communication is cheap but
attention/compute is metered.

## Q2 — chain-of-command (→ hard tree + its relief valves)

Designing message-routing rules for a hierarchical org of AI agents
where members may only message within their team or through their lead
(strict chain of command). Research how human organizations handle the
known weaknesses of strict hierarchical communication: (1) Team
Topologies (Skelton/Pais) interaction modes and where explicit team
APIs beat ad-hoc chatter; (2) Conway's law implications for structuring
teams around a codebase; (3) mechanisms that relieve hierarchy without
destroying it — skip-level meetings, gemba walks / management by
walking around, liaison roles, tiger teams; (4) documented failure
modes of strict chains: information silos, bad-news filtering,
communication-failure post-mortems (Challenger, etc.). Deliver: which
relief valves are essential versus optional, the evidence for each, and
specific triggers for when a strict hierarchy should be pierced.

## Q3 — intent-documents (→ MISSION.md contract)

For an autonomous AI-agent organization that must work toward
milestones without a human issuing tasks: research how humans encode
INTENT so subordinates can act autonomously. Cover: (1) military
mission command / commander's intent — how intent statements are
written, briefbacks/confirmation briefs, and why intent beats detailed
orders under uncertainty; (2) Basecamp Shape Up — appetite versus
estimate, pitches, circuit breakers; (3) Amazon working-backwards
memos and handbook-first cultures (GitLab) as single-source-of-truth
practices; (4) evidence on OKR failure modes when cascaded
mechanically. Deliver: patterns for a SHORT human-owned intent
document that agents re-read every working session — what belongs in
it, what must be kept out, how often it should change, and the known
failure modes (stale intent, over-specification).

## Q4 — goodhart-throttles (→ budget design, prose-only milestones)

In an AI-agent organization, every mechanized metric so far got gamed
by the agents (turn budgets caused premature answer-closing, etc.).
Research the human evidence: (1) Goodhart's law / Campbell's law case
studies in management — targets gamed in call centers, policing stats,
healthcare waiting-time targets; (2) which control mechanisms degrade
gracefully — kanban WIP limits, rate limiting, budget caps that
throttle rather than punish — and why constraints on FLOW get gamed
less than targets on OUTCOMES; (3) principal-agent theory takeaways
for designing incentives when you cannot audit everything. Deliver:
design rules for governance that agents cannot usefully game, with
sources, and the specific reasons capacity throttles resist gaming
better than quotas and targets.

## Q5 — batch-cadence (→ batch invoke, idle cadences by role)

Designing the communication cadence for an org of AI agents that wake,
read ALL queued messages at once, do a unit of work, and sleep — batch
processing rather than interrupt-driven. Research the human analogues
and their measured effects: (1) async-first/remote-first practices at
GitLab, Basecamp, Automattic — written decision records, handbook
culture, no-meeting norms; (2) research on interruption cost and
batched communication (email-checking cadence studies), maker versus
manager schedules (Paul Graham); (3) cadenced synchronization points —
standups, weekly reviews, sprint boundaries — and which cadences suit
coordinators versus makers. Deliver: recommended wake-cadence
heuristics by role (lead versus worker), the evidence that
batch-reading beats per-message interrupts, and the pitfalls of
over-batching (stale responses, deadlock waits).
