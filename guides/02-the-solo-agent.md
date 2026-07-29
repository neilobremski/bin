# Chapter 2 — The Solo Agent

## 1. Capability

At the end of this chapter you will have **Wren**: a roster of one — a
single AI member behind r4t, with spend budgets, a swappable rig, and a
conversation that persists across turns. You will prove the persistence
with a codeword, break the configuration on purpose, and read the
fail-closed error that stops it from ever half-running.

## 2. Time

About 30 minutes, plus a one-time model download on the free path.

## 3. Starting state

- Chapter 1 complete: `hello` and `me` registered, `ark` shows the a8s
  section green.
- One harness, depending on your path:
  - **Free path** — `ollama` installed and serving, with the model pulled:

**Run**

```bash
ollama pull qwen3.6
```

  - **Subscription path** — the Cursor agent CLI (`agent`) installed and
    logged in.

`ark doctor` should show your chosen harness green before you continue.

## 4. The change

A team is two files with two jobs: `ROSTER.md` in the team repo says *who*
exists, and `~/.config/r4t/rigs.json` — outside the repo, where a repo
edit can't reach it — says what each member's **rig** actually runs. Make
the team directory and let r4t write the starters:

**Run**

```bash
mkdir -p ~/ark/solo
cd ~/ark/solo
r4t init
```

You should see:

```
roster: wrote starter /home/you/ark/solo/ROSTER.md
rig config: wrote starter /home/you/.config/r4t/rigs.json

Register and start the team (a namespace prefix cannot share a
name with its agent, so the node is registered as <team>-node):

  a8s add solo-node /home/you/ark/solo /home/you/bin/apps/r4t/example-definition.json
  a8s namespace solo solo-node
  a8s start solo-node
  tell solo "hello"            # bare namespace -> roster leader
  tell solo:dev "hello"        # namespace:member -> specific member
```

The starter roster is a three-member team. Ours is smaller. Replace it
with a roster of one AI and one human — you:

**Replace** `~/ark/solo/ROSTER.md` (whole file)

```markdown
# Team Roster

### You
- **Status:** Human
- **Role:** Owner

### Wren
- **Status:** AI
- **Rig:** solo
- **Leader:** yes
- **Continue:** on
- **Workdir:** agents/wren
- **Role:** The solo agent — does the work and answers the owner

Wren is a roster of one: leader, developer, and correspondent in a single
seat. Keep answers short and concrete.
```

Four lines carry the weight. `Leader: yes` — external mail enters at Wren.
`Rig: solo` — a symbolic name; what it runs comes next, from outside the
repo. `Continue: on` — Wren's turns resume its CLI's own conversation
instead of starting cold every wake. `Workdir: agents/wren` — Wren gets its
own subfolder, so its conversation and files never collide with a future
teammate's.

Now define the `solo` rig. Pick your path — and note that these two
presets are only the blessed pair: the other popular harness CLIs are
presets too (`r4t rig presets` lists them, and later guide branches may
walk through more of them), added by the same one-line command.

**Run** (free path)

```bash
r4t rig add solo opencode-ollama --model qwen3.6
r4t rig set solo echo true
```

You should see:

```
added rig 'solo' (opencode-ollama) to /home/you/.config/r4t/rigs.json
  invoke: ollama launch opencode --model qwen3.6 -- run --auto --dir . {prompt}
Reference it from ROSTER.md: `- **Rig:** solo`
set solo echo = true in /home/you/.config/r4t/rigs.json
```

**Run** (subscription path)

```bash
r4t rig add solo cursor
r4t rig set solo echo true
```

You should see:

```
added rig 'solo' (cursor) to /home/you/.config/r4t/rigs.json
  invoke: agent -p --trust --force --approve-mcps {prompt}
Reference it from ROSTER.md: `- **Rig:** solo`
set solo echo = true in /home/you/.config/r4t/rigs.json
```

No `--model` is the deliberate part: the invoke line carries no model
flag, so the harness runs whatever your Cursor subscription defaults to —
covered by what you already pay. Pin one with `--model <name>` and you
can land on a frontier model billed as usage-based credits, which a
chatty agent burns through fast; add the flag only when you mean to.
(`agent models` lists what your account can run.)

`echo true` makes Wren **stdout-only**: its turn prompt carries no
messaging doctrine, and whatever it prints becomes its one reply to you.
That is the right shape for a roster of one — Wren has nobody to message
but you — and it sidesteps a real failure mode: without echo, a member
must send its reply with `tell`, and prose answers under ~80 characters
are discarded as terminal chrome. Chapter 4 lifts echo when the team
grows.

Lint before going live — r4t fails closed on any roster/rig disagreement:

**Run**

```bash
cd ~/ark/solo
r4t roster check
```

You should see:

```
You: note — Human without an Address (team cannot tell them)
/home/you/ark/solo/ROSTER.md: OK (2 member(s), leader Wren)
```

The note is expected: you have no a8s doorbell address yet, so the team
can't ring you when you're away. You'll read your mail at the seat
instead. Finally, register the node on a8s exactly as `r4t init` printed:

**Run**

```bash
a8s add solo-node ~/ark/solo ~/bin/apps/r4t/example-definition.json
a8s namespace solo solo-node
a8s start solo-node
```

You should see:

```
added solo-node -> /home/you/ark/solo
definition: /home/you/bin/apps/r4t/example-definition.json  (explicit)
bound solo: -> solo-node
started solo-node as PID 23851
```

## 5. Run it

You are the roster's Human, and r4t gives you a **seat**: send as
yourself, and read what parks for you. `seat send` runs Wren's turn
synchronously — the first turn takes a minute on the free path while the
model loads; later turns take seconds.

**Run**

```bash
cd ~/ark/solo
r4t seat send --node solo "In one sentence: what is your job on this team?"
r4t seat inbox --node solo
```

(`--node solo` is needed the first time; once the team has dispatched a
turn, r4t finds the node from inside the repo on its own.)

## 6. Expected receipt

You should see:

```
── from solo:wren (2026-07-29T04:55:04.121285Z)
My job is to do the work and answer to the owner — handling everything from leadership to development as the sole member of the solo team.
```

Wren read its own roster block and answered in character. Now the proof
that `Continue: on` means what it says — plant a codeword in one turn:

**Run**

```bash
r4t seat send --node solo "Remember this codeword: TIDEPOOL. Confirm you have it."
r4t seat inbox --node solo
```

You should see:

```
── from solo:wren (2026-07-29T04:55:15.702909Z)
Codeword confirmed: TIDEPOOL.
```

And ask for it back in a second, separate turn:

**Run**

```bash
r4t seat send --node solo "What was the codeword?"
r4t seat inbox --node solo
```

You should see:

```
── from solo:wren (2026-07-29T04:55:30.722365Z)
TIDEPOOL.
```

Two processes, two wakes, one conversation. That continuity is what
`Continue: on` buys.

## 7. Break it

`Continue: on` needs a rig whose CLI can actually resume a conversation.
Swap Wren's rig to the bare `ollama` preset — a raw model prompt, no
session store, no continue support:

**Run**

```bash
r4t rig swap solo ollama --model qwen3.6
r4t roster check
```

You should see:

```
swapped rig 'solo' to ollama in /home/you/.config/r4t/rigs.json
  invoke: ollama run qwen3.6 {prompt}
You: note — Human without an Address (team cannot tell them)
Wren: Wren has Continue: on but rig 'solo' does not support it (preset ollama; presets that continue: agy, claude, codex, cursor, opencode, opencode-ollama) — try: r4t rig swap solo <preset>
1 problem(s)
```

## 8. Diagnose

Read the error end to end — it names the member, the rig, the reason, the
presets that would work, and the exact command to run. Exit code is 1, and
the same check runs at dispatch: a member in this state **does not run**,
and whoever messages it is told why. r4t never silently downgrades
`Continue: on` to cold prompts — that would look like working while
quietly lobotomizing your agent every turn.

## 9. Fix

Take the error's suggestion:

**Run**

```bash
r4t rig swap solo opencode-ollama --model qwen3.6
r4t roster check
```

(Subscription path: swap back to `cursor` instead.)

You should see:

```
swapped rig 'solo' to opencode-ollama in /home/you/.config/r4t/rigs.json
  invoke: ollama launch opencode --model qwen3.6 -- run --auto --dir . {prompt}
You: note — Human without an Address (team cannot tell them)
/home/you/ark/solo/ROSTER.md: OK (2 member(s), leader Wren)
```

## 10. Check

The codeword is the health check. Ask again:

**Run**

```bash
r4t seat send --node solo "What was the codeword?"
r4t seat inbox --node solo
```

You should see:

```
── from solo:wren (2026-07-29T04:55:49.122781Z)
TIDEPOOL.
```

Wren still knows. The team is whole.

## 11. Customize

One line in the roster: bound how long Wren's conversation may sit idle.

**Replace** `~/ark/solo/ROSTER.md` — in Wren's block, directly under
`- **Continue:** on`, add:

```markdown
- **Flush:** 4h
```

A conversation idle past four hours is retired — Wren is prompted once to
write its state to disk, and the next real message founds a fresh
conversation from that saved state. It keeps a long-lived agent from
dragging weeks of stale context into every turn; the full mechanics are
chapter 3's subject.

**Run**

```bash
r4t roster check
```

You should see:

```
You: note — Human without an Address (team cannot tell them)
/home/you/ark/solo/ROSTER.md: OK (2 member(s), leader Wren)
```

## 12. Commit point

The roster is repo state; commit it. (The rig config stays outside the
repo by design — that split is what makes a roster edit unable to smuggle
in commands.)

**Run**

```bash
cd ~/ark/solo
git init -q
git add ROSTER.md
git commit -q -m "solo roster: Wren, continue on, flush 4h"
```

Copy-paste templates for this chapter's final state live in
[templates/02-solo-opencode-ollama/](templates/02-solo-opencode-ollama/)
and [templates/02-solo-cursor/](templates/02-solo-cursor/).
