# Scenario B — Nested gardens (departments with walls)

**Metaphor:** a company of small companies. Each department has its own
office, its own manager, its own budget — and from the outside, a single
front door.

## What it looks like

Each department is a **complete r4t team of its own**: its own
`ROSTER.md`, its own a8s node, its own governance state. The parent
roster lists the whole department as *one member* whose address is the
department's namespace:

```markdown
# s1l/ROSTER.md (the executive team)

### Gerry
- **Status:** AI
- **Rig:** orchestrator
- **Leader:** yes

### Engineering
- **Status:** Team
- **Address:** s1l-eng

### Art
- **Status:** Team
- **Address:** s1l-art

### Neil
- **Status:** Human
- **Address:** neil-phone
```

```markdown
# s1l-eng/ROSTER.md (inside the engineering garden)

### Tariq
- **Rig:** specialist
- **Leader:** yes

### Phil
- **Rig:** specialist
...
```

Gerry writes `tell engineering "we need the deploy lane verified"`. What
happens inside — Tariq delegating to Phil, three turns of back-and-forth,
a suppressed loop — is engineering's business. What comes back out is
one distilled answer.

## Why this is the "already built" option

The egress rule was designed for exactly this: headers are stripped at
the wall because *"other nodes must not need to know whether a name is
one agent, a human, a device, or a whole roster."* A department IS a
roster wearing a name badge. No new routing machinery is required —
this composes from existing pieces today.

## What you get

- **Real storm containment.** A meltdown inside engineering burns
  engineering's budgets and dead-letters into engineering's files. The
  executive team never even slows down.
- **Real compression.** Each wall only passes finished thoughts. This is
  the summarize-up/expand-down behavior real org charts exist to create.
- **Independent tuning.** The art department can run cheap fast models
  on a loose leash; platform can run expensive careful ones strictly.

## What it costs

- **More nodes to run** (one per department), more `a8s start`, more
  places to look — though `r4t status` can learn to roll up children.
- **Cross-department chatter is expensive by design.** Phil cannot ping
  Zoe directly; the request goes up through Tariq, across, and down.
  That friction is mostly the point, but it makes genuinely
  cross-cutting work (a bug spanning client and server) slower.
- **Repo layout question:** departments of one product share one git
  repo. Multiple nodes can point at the same root, but working-tree
  collisions between departments become a real concern (worktrees may
  be the answer; undecided).

## When this wins

When the roster is genuinely large (15+), when departments have
different risk profiles, or when you want failures *contained* rather
than merely slowed.

## When it loses

For a 6-member team it's bureaucracy — three offices for five people.
And it does nothing about chaos *inside* a department; each garden still
needs scenario A or C internally once it grows.
