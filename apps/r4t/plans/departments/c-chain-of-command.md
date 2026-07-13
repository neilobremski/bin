# Scenario C — Chain of command (the reporting tree)

**Metaphor:** a normal company org chart. You can talk to your
teammates, your manager, and (if you're a manager) your peers. You do
not DM the CEO's other departments.

## What it looks like

One roster, one node — scenario A's `Team:` label plus one more:
each team names a **lead**, and dispatch *enforces* the tree instead of
merely suggesting it:

```markdown
### Gerry
- **Status:** AI
- **Rig:** orchestrator
- **Leader:** yes

### Tariq
- **Status:** AI
- **Rig:** specialist
- **Team:** platform
- **Lead:** yes          ← lead of his team

### Phil
- **Status:** AI
- **Rig:** specialist
- **Team:** platform     ← reports to Tariq
```

The routing rule, in English: **a member may message their own team and
their lead; a lead may message their team, the other leads, and the
leader; the leader may message the leads and the human.** A release that
violates the tree isn't dropped — it's *redirected to the sender's lead*
with a note ("Phil tried to reach Zoe about X"), the way a good EA
forwards a misdirected email.

## Why this is interesting beyond tidiness

The hop counter — the thing that cut a message in front of you today —
exists to stop infinite loops. **A tree cannot loop.** With an enforced
tree, hop limits can retire entirely, and with them the whole category
of "your work died on an accounting rule." Structure replaces the
guillotine. (The repeat suppressor stays, for the ping-pong case
between a member and their own lead.)

It also fixes leader overload mechanically: Gerry's inbox can only
contain leads and you. Twenty-one members, but his world is four names.

## What you get

- Real narrowing of traffic without extra nodes or processes.
- Hop limits become unnecessary — the least-loved governor retires.
- The observability story gets *better*: "stuck" now has an address
  ("platform's lead hasn't reported in 20 minutes") instead of a task id.

## What it costs

- One shared budget economy still (a storm inside platform still spends
  from the common pool — pair with member-capacity budgets to fix).
- Dispatch grows a policy engine, and redirection needs care: done
  naively it turns leads into bottlenecks-by-forwarding.
- Cross-team work always transits two leads. Same friction as B, same
  "that's mostly the point."

## When this wins

Mid-size rosters (7–15) in one repo where you want structure *and* one
window to watch. It is probably the natural inside-a-garden shape even
if B is adopted between gardens.

## When it loses

Tiny teams don't need it. And it is a *policy* change to dispatch —
more code and more tests than A or B, which get their shape for free.
