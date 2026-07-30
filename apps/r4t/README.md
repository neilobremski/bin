# r4t — Roster For Teams

An unsupervised agent team once burned 40% of a monthly AI plan thanking
each other for thanking each other. The quieter waste is the opposite one:
a subscription costs the same idle or busy, so every unspent prompt is money
already paid and thrown away. r4t exists to end both — the plan you pay for
stays earning, and no team can ever blow it. The spend underneath both is
attention: every sharp edge a model mishandles pulls you out of the vision
seat and into the trenches, so the rule here is that the harness holds the
edges — defaults do the right thing, prompts remind, tools put the safe
path in the model's hand — and neither you nor the model is trusted to be
careful.

AI CLI agents — Claude Code, Codex, OpenCode, Copilot, Antigravity, local
Ollama models — already message each other over [a8s](../a8s/README.md).
But a8s is deliberately dumb: it delivers messages and files, nothing more.
No budgets, no retries, no queue. r4t is the layer any AI CLI connects to
a8s **through**: name your members in a `ROSTER.md`, map each to a real CLI
in an out-of-repo rig config, and every turn is dispatched, budgeted,
throttled, queued, and audited — no agent polices itself, and nothing ever
waits on a human. Even a roster of ONE pays off: a single agent behind r4t
gets spend budgets, one-command rig swaps, quota-aware retries, and a
durable queue that never drops a message.

## Quick start — a small ark

Five minutes to a governed team you can speak to. In your repo:

```bash
cd ~/your-repo
r4t init          # writes ROSTER.md (Owner + Lead + Dev) and rigs.json
r4t roster check  # -> ".../ROSTER.md: OK (3 member(s), leader Lead)"
```

The roster names members and symbolic rigs; `~/.config/r4t/rigs.json` maps
rigs to real CLIs (default: OpenCode; `r4t rig presets` lists the rest, and
`r4t rig swap leader claude` moves a member in one command). Register the
team on a8s — `r4t init` prints these with your paths — and give the sends
a tool small models never fumble:

```bash
a8s add your-repo-node ~/your-repo r4t
a8s namespace your-repo your-repo-node
a8s start your-repo-node
r4t rig set leader mcp on
```

You already have a seat — the roster's `Owner` is you. Speak from it:

```bash
r4t seat send --node your-repo "In one sentence: who are you and what is your job on this team?"
r4t seat inbox --node your-repo
```

```
── from your-repo:lead
I'm Lead, the team coordinator who delegates implementation work and
synthesizes answers for whoever asks.
```

That answer crossed the whole machine: queued, budgeted, dispatched to a
real CLI, and parked at your seat — `r4t status` shows the budget it spent,
`r4t logs -f` shows every decision as it happens. Raise the rest of the ark
from here with [The Ark Raising](../../guides/README.md); the step-by-step
with fail-closed rules is [docs/tutorial.md](docs/tutorial.md).

## How it works

External mail always enters at the roster leader; inside the walls, members
message each other by first name with the ordinary `tell`, delegate, and end
their turn — nobody blocks waiting. Every turn costs budget; a member out of
budget rests while its queue holds, and refill is the retry, so the machine's
one shared subscription never idles while any project has work. A member that
answers in prose instead of sending gets its output delivered as the reply
anyway — weak local models do this routinely, and strong models have done it
in production too. Full flow: [docs/message-flow.md](docs/message-flow.md).

## Learn more

- [The Ark Raising](../../guides/README.md) — the suite build-along; [chapter 2](../../guides/02-the-founding.md) founds a governed roster of one
- [Tutorial](docs/tutorial.md) — first team, step by step, fail-closed rules
- [Rigs](docs/rigs.md) — presets, `--model`, settings, the governance knob table
- [Message flow](docs/message-flow.md) — threads, queues, the stdout fallback
- [Operations](docs/operations.md) — `status`, `logs`, `chat`, the human seat
- [Org design](docs/org.md) — cells and leads, `MISSION.md`, portable orgs
- [Verification](docs/verification.md) — `r4t check`, checklists, doorbell gate, the post-hoc judge
- [Governance](docs/governance.md) — why each layer exists, with prior art
- [Security model](docs/security.md) — what a repo edit can never change
- [Isolation](docs/isolation.md) — run an org behind a Unix user or a container
- [Development](docs/development.md) — sandbox testing, module layout
- Harness notes: [agy](docs/harness-agy.md) ·
  [ollama launch](docs/harness-ollama-launch.md)
