# Scenario D — Dynamic squads (the org chart is a bench)

**Metaphor:** a film production. There is no permanent "camera
department that always works with the same actors" — a producer
assembles exactly the crew each shoot needs, they work, they disband.

## What it looks like

The roster stops being an org chart and becomes a **bench** of available
specialists. When work arrives, the leader *forms a squad*: picks 2–4
members, names the goal, and r4t scopes a workspace to that squad — its
own thread, its own budget, its own tight prompt ("you are working with
Phil and Elena on X; report to Gerry").

```
you  -> gerry: "two browser windows fight over one session"
gerry -> SQUAD(phil, elena): goal="isolate per-window sessions",
                              report_to=gerry
   ... squad chatter, contained ...
squad -> gerry: "fixed; localStorage key now per-tab; verified"
gerry -> you: one answer
```

When the squad reports, it dissolves. Nothing about it persists except
the work and the report.

## Why this matches how you actually think

Your instinct — *"each agent simply has their queue or workload…
messages should flow so they can get things done"* — is this scenario.
The durable things are members and their workloads; collaboration is
temporary and purpose-built. A "task" stops being an accounting cage
and becomes what it always wanted to be: a squad's reason to exist.

## What you get

- The tightest prompts of any scenario — small casts demonstrably keep
  small models coherent (the ttt experiments showed this clearly).
- Storms can't spread past a squad's walls, and a squad has a natural
  end — "done" is structural, not a timeout.
- The bench scales: adding a 22nd specialist costs nothing until
  someone casts them.

## What it costs

- **The most new machinery of any option**: squad lifecycle (form,
  work, report, dissolve), squad-scoped budgets, observability that
  shows squads instead of members.
- The leader becomes an org designer every single day — and leader
  quality is already the scarcest resource. A weak leader forms bad
  squads; nothing downstream can compensate.
- Standing responsibilities fit awkwardly: "Dexter owns QA, always" has
  no natural home on a bench. Real orgs run departments *and* tiger
  teams for a reason.

## When this wins

When work is genuinely episodic — discrete asks arriving at a leader,
each needing a different skill mix. It is the best long-run answer to
"one synthesized answer comes back," because the whole structure exists
per-question.

## When it loses

As the *only* model. Continuous lanes (QA, ops, the sprint board
itself) want standing owners. This likely composes ON TOP of A or C —
squads drawn from labeled teams — rather than replacing them. It should
probably be built last, after a shape exists.
