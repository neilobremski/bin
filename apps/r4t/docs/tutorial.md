# Getting started with r4t

This tutorial walks a new developer through their first team on a fresh
machine: what the two config files mean, how to define rigs, and
what happens when the roster and rig config disagree.

For governance rationale see [governance.md](governance.md). For the knob
table see [README.md](../README.md#governance-knobs).

## Two files, two jobs

| File | Where | What it defines |
|------|-------|-----------------|
| **`ROSTER.md`** | In the team repo | *Who* is on the team and which **symbolic rig** each AI member uses |
| **`~/.config/r4t/rigs.json`** | Out of repo (`R4T_HOME`) | What each rig **actually runs** — CLI argv, timeouts, hop limits |

The roster never contains shell commands. A line like `Harness: opencode` is
wrong — `opencode` is a CLI, not a rig name. You write `Harness: worker`
and define `worker` in the rig config.

This split is deliberate: the in-repo roster cannot smuggle in arbitrary
commands. Only rigs declared in the out-of-repo rig config can execute.

## Fresh machine walkthrough

### 1. See where you stand

```bash
r4t
```

With no arguments, r4t prints local status: `R4T_HOME`, rig config path,
configured rigs, registered teams, whether the current directory has a
roster, available commands, and suggested next steps.

### 2. Bootstrap the repo and rig config

```bash
cd ~/my-team-repo
r4t init
```

`r4t init` writes (if missing):

- **`ROSTER.md`** — a Human owner, an AI Lead on rig `leader`, an AI Dev on
  rig `member`
- **`~/.config/r4t/rigs.json`** — matching `leader` and `member` rig
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

### 3. Add rigs

Rig **names** are yours (`leader`, `member`, `reviewer`, `worker`, …).
**Presets** are CLI templates aligned with [a8s definitions](../../a8s/definitions/)
(`claude`, `cursor`, `opencode`, `agy`, `codex`, `copilot`).

```bash
r4t rig presets                              # list presets + invoke lines
r4t rig add reviewer claude                # add rig "reviewer" from preset
r4t rig add worker opencode
r4t rig add local opencode-ollama --model qwen2.5-coder:7b
r4t rig add lead cursor --force            # replace an existing rig
```

Each preset documents its **headless** entry point (`-p`, `--print`, `run
--auto`, etc.) so r4t turns never open an interactive session.

### 4. Wire the roster to those rigs

Edit `ROSTER.md`. Each AI member needs a `Rig:` line naming a rig that
exists in `rigs.json`:

```markdown
### Reviewer
- **Status:** AI
- **Rig:** reviewer
- **Role:** Code reviewer
```

Humans use `Status: Human` and never get a rig. Optional
`Address:` is their a8s name for outbound tells.

### 5. Lint before going live

```bash
r4t roster check      # roster shape + every Harness resolves to a rig
r4t rig list      # rigs, invoke lines, and roster resolution
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
ROSTER.md                      rigs.json
───────────                    ────────────────
Lead   → Harness: leader    →  "leader":  { invoke: [...] }
Dev    → Harness: member    →  "member":  { invoke: [...] }
Reviewer → Harness: reviewer → "reviewer": { ... }   ← you must add this
```

**Preset names** (`claude`, `opencode`, …) populate rig definitions via
`r4t rig add`. **Rig names** (`leader`, `reviewer`, …) are what the
roster references.

Optional **pins** in `rigs.json` override a member's roster harness
silently — an in-repo roster edit cannot upgrade a pinned agent:

```json
"pins": { "gerry": "leader" }
```

## Missing rig? No default — fail closed

There is **no fallback harness**. If `ROSTER.md` names a rig that is not
defined in `rigs.json`, that member **does not run**.

### At check time

```bash
$ r4t roster check
Reviewer: rig 'reviewer' not found in /Users/you/.config/r4t/rigs.json (fail closed) — try: r4t rig add reviewer <preset>
1 problem(s)
```

Exit code 1.

### At runtime

When a message targets that member, r4t tells the **sender** and never
spawns the harness:

```
Reviewer cannot run: rig 'reviewer' not found in /Users/you/.config/r4t/rigs.json (fail closed) — try: r4t rig add reviewer <preset>
```

The same applies when the rig config file is entirely missing:

```
Dev cannot run: rig config not found at ~/.config/r4t/rigs.json — rig 'member' cannot be resolved (fail closed)
```

### What exists by default

Only what `r4t init` creates: **`leader`** and **`member`** rigs, wired to
the starter roster. Any other rig name must be added explicitly:

```bash
r4t rig add junior-dev opencode
```

## How a message flows

1. `tell myrepo:dev "..."` routes through a8s to the team node; the node's
   definition invokes `r4t dispatch`.
2. r4t loads the roster, resolves Dev's rig, checks governance
   gates, and runs the rig's CLI with a prompt (persona, history, incoming
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
| `r4t init` | Starter `ROSTER.md` + `~/.config/r4t/rigs.json` |
| `r4t rig presets` | Named CLI templates (from a8s definitions) |
| `r4t rig add <rig> <preset>` | Define a rig in the rig config |
| `r4t rig list` | Show rigs and how roster members resolve |
| `r4t roster check` | Lint roster and rig mappings |
| `r4t status --node <team>` | Live locks, buckets, tasks, dead letters |
| `r4t sandbox --fake` | End-to-end plumbing test without LLM calls |
| `r4t sandbox --preset opencode-ollama --model M` | Live sandbox via local Ollama + OpenCode (stderr progress, report on stdout) |

## Example: existing repo

```bash
r4t init --root ~/repos/s1l           # keeps an existing ROSTER.md if present
r4t rig add junior-dev opencode   # if roster references junior-dev
r4t roster check
a8s add s1l-node ~/repos/s1l ~/bin/apps/r4t/example-definition.json
a8s namespace s1l s1l-node
a8s start s1l-node
tell s1l:gerry "Ship the refactor; report when reviewed."
```

Watch: `a8s logs s1l-node -f`, `r4t status --node s1l`, and dead letters
under `~/.config/r4t/teams/s1l/dead-letter/`.
