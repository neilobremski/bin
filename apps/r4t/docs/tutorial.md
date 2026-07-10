# Getting started with r4t

This tutorial walks a new developer through their first team on a fresh
machine: what the two config files mean, how to define harness tiers, and
what happens when the roster and harness config disagree.

For governance rationale see [governance.md](governance.md). For the knob
table see [README.md](../README.md#governance-knobs).

## Two files, two jobs

| File | Where | What it defines |
|------|-------|-----------------|
| **`ROSTER.md`** | In the team repo | *Who* is on the team and which **symbolic tier** each AI member uses |
| **`~/.r4t/harnesses.json`** | Out of repo (`R4T_HOME`) | What each tier **actually runs** — CLI argv, timeouts, hop limits |

The roster never contains shell commands. A line like `Harness: opencode` is
wrong — `opencode` is a CLI, not a tier name. You write `Harness: worker`
and define `worker` in the harness config.

This split is deliberate: the in-repo roster cannot smuggle in arbitrary
commands. Only tiers declared in the out-of-repo harness config can execute.

## Fresh machine walkthrough

### 1. See where you stand

```bash
r4t
```

With no arguments, r4t prints local status: `R4T_HOME`, harness config path,
configured tiers, registered teams, whether the current directory has a
roster, available commands, and suggested next steps.

### 2. Bootstrap the repo and harness config

```bash
cd ~/my-team-repo
r4t init
```

`r4t init` writes (if missing):

- **`ROSTER.md`** — a Human owner, an AI Lead on tier `leader`, an AI Dev on
  tier `member`
- **`~/.r4t/harnesses.json`** — matching `leader` and `member` tier
  definitions (default invoke: `opencode run --auto --dir .`)

It then prints the exact **a8s registration** sequence for your repo name:

```bash
a8s add myrepo-node /path/to/my-team-repo ~/bin/apps/r4t/example-definition.json
a8s namespace myrepo myrepo-node
a8s start myrepo-node
tell myrepo-node "hello"       # bare node name -> roster leader
tell myrepo:dev "hello"        # namespace prefix -> specific member
```

Run those commands (adjust paths and names) before expecting live traffic.

### 3. Add harness tiers

Tier **names** are yours (`leader`, `member`, `reviewer`, `worker`, …).
**Presets** are CLI templates aligned with [a8s definitions](../../a8s/definitions/)
(`claude`, `cursor`, `opencode`, `agy`, `codex`, `copilot`).

```bash
r4t harness presets                              # list presets + invoke lines
r4t harness add reviewer claude                # add tier "reviewer" from preset
r4t harness add worker opencode
r4t harness add local opencode-ollama --model qwen2.5-coder:7b
r4t harness add lead cursor --force            # replace an existing tier
```

Each preset documents its **headless** entry point (`-p`, `--print`, `run
--auto`, etc.) so r4t turns never open an interactive session.

### 4. Wire the roster to those tiers

Edit `ROSTER.md`. Each AI member needs a `Harness:` line naming a tier that
exists in `harnesses.json`:

```markdown
### Reviewer
- **Status:** AI
- **Harness:** reviewer
- **Role:** Code reviewer
```

Humans use `Status: Human` and never get a harness tier. Optional
`Address:` is their a8s name for outbound tells.

### 5. Lint before going live

```bash
r4t roster check      # roster shape + every Harness resolves to a tier
r4t harness list      # tiers, invoke lines, and roster resolution
```

Fix anything `roster check` reports before registering the team or sending
work.

### 6. Operate

```bash
r4t status --node myrepo    # locks, buckets, tasks, dead letters
a8s logs myrepo-node -f     # traffic + r4t governance lines
```

## Mental model

```
ROSTER.md                      harnesses.json
───────────                    ────────────────
Lead   → Harness: leader    →  "leader":  { invoke: [...] }
Dev    → Harness: member    →  "member":  { invoke: [...] }
Reviewer → Harness: reviewer → "reviewer": { ... }   ← you must add this
```

**Preset names** (`claude`, `opencode`, …) populate tier definitions via
`r4t harness add`. **Tier names** (`leader`, `reviewer`, …) are what the
roster references.

Optional **pins** in `harnesses.json` override a member's roster harness
silently — an in-repo roster edit cannot upgrade a pinned agent:

```json
"pins": { "gerry": "leader" }
```

## Missing tier? No default — fail closed

There is **no fallback harness**. If `ROSTER.md` names a tier that is not
defined in `harnesses.json`, that member **does not run**.

### At check time

```bash
$ r4t roster check
Reviewer: tier 'reviewer' not found in harness config /Users/you/.r4t/harnesses.json (fail closed)
1 problem(s)
```

Exit code 1.

### At runtime

When a message targets that member, r4t tells the **sender** and never
spawns the harness:

```
Reviewer cannot run: tier 'reviewer' not found in harness config /Users/you/.r4t/harnesses.json (fail closed)
```

The same applies when the harness config file is entirely missing:

```
Dev cannot run: harness config not found at ~/.r4t/harnesses.json — tier 'member' cannot be resolved (fail closed)
```

### What exists by default

Only what `r4t init` creates: **`leader`** and **`member`** tiers, wired to
the starter roster. Any other tier name must be added explicitly:

```bash
r4t harness add junior-dev opencode
```

## How a message flows

1. `tell myrepo:dev "..."` routes through a8s to the team node; the node's
   definition invokes `r4t dispatch`.
2. r4t loads the roster, resolves Dev's harness tier, checks governance
   gates, and runs the tier's CLI with a prompt (persona, history, incoming
   message).
3. The harness's `$TELL_OUTBOX_DIR` points at a per-turn staging dir. The
   agent replies with ordinary `tell`. After the turn, r4t releases staged
   envelopes: stamps headers, applies quotas, writes history, moves messages
   to the node's outbox for a8s.
4. Agents never wait for replies inside a turn. Delegate, end the turn, get
   woken when replies arrive, answer the originator when there is enough.

## Command reference

| Command | Purpose |
|---------|---------|
| `r4t` | Local status, harness summary, next steps |
| `r4t init` | Starter `ROSTER.md` + `~/.r4t/harnesses.json` |
| `r4t harness presets` | Named CLI templates (from a8s definitions) |
| `r4t harness add <tier> <preset>` | Define a tier in the harness config |
| `r4t harness list` | Show tiers and how roster members resolve |
| `r4t roster check` | Lint roster and tier mappings |
| `r4t status --node <team>` | Live locks, buckets, tasks, dead letters |
| `r4t sandbox --fake` | End-to-end plumbing test without LLM calls |
| `r4t sandbox --preset opencode-ollama --model M` | Live sandbox via local Ollama + OpenCode |

## Example: existing repo

```bash
r4t init --root ~/repos/s1l           # keeps an existing ROSTER.md if present
r4t harness add junior-dev opencode   # if roster references junior-dev
r4t roster check
a8s add s1l-node ~/repos/s1l ~/bin/apps/r4t/example-definition.json
a8s namespace s1l s1l-node
a8s start s1l-node
tell s1l:gerry "Ship the refactor; report when reviewed."
```

Watch: `a8s logs s1l-node -f`, `r4t status --node s1l`, and dead letters
under `~/.r4t/teams/s1l/dead-letter/`.
