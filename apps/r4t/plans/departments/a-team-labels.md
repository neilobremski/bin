# Scenario A — Team labels (the seating chart)

**Metaphor:** an open-plan office where you put teams at adjacent desks.
Everyone can still shout across the room, but the seating chart nudges
who talks to whom.

## What it looks like

One `ROSTER.md`, one node, one new optional field — exactly the
`Team:` label you suggested:

```markdown
### Gerry
- **Status:** AI
- **Rig:** orchestrator
- **Leader:** yes

### Tariq
- **Status:** AI
- **Rig:** specialist
- **Team:** platform

### Phil
- **Status:** AI
- **Rig:** specialist
- **Team:** gameplay

### Elena
- **Status:** AI
- **Rig:** specialist
- **Team:** gameplay
```

## What changes in behavior

Mostly the **prompt**. Each turn tells the agent who its teammates are;
with labels, that list shrinks to *your own team, plus the leader*.
Phil's prompt names Elena and Gerry — it simply never mentions Tariq.
Agents overwhelmingly message people their prompt names, so the traffic
graph narrows without any enforcement.

Optionally, `r4t status` and chat group their displays by team, which
alone makes a 21-member wall of text readable.

## What it does NOT do

- No enforcement. A model that learned "tell tariq" from history can
  still do it (the release still goes through).
- No storm containment. One shared budget economy, one shared blast
  radius — a gameplay-team storm still drains the same pool platform
  draws from.
- No compression. The leader still receives every report individually.

## When this wins

As a first step, and as the vocabulary for anything later: scenarios C
and D both *reuse* this label. It is an afternoon of work, changes no
architecture, and makes the observability surfaces dramatically calmer.
Worst case it's harmless; best case it's most of the benefit.

## When it loses

If the real problem is economic (one storm starving everyone) rather
than social (who talks to whom), labels won't save you — the desks are
closer but the building still shares one fuse box.
