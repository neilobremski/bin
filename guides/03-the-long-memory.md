# Chapter 3 — The Long Memory

## 1. Capability

At the end of this chapter Wren survives conversation death. You will watch
the **flush cycle** end an idle conversation on purpose — Wren is prompted
once to write his state to `STATUS.md`, the conversation is retired, and
the next real message **refounds** him from that file. Then you will do
something harsher: swap Wren's rig to a different CLI mid-life and watch him
come back coherent on the other side. The conversation is disposable; the
agent is not.

## 2. Time

About 30 minutes, most of it deliberate waiting.

## 3. Starting state

- Chapter 2 complete: Wren answers at the seat, `Continue: on`, and the
  customize step left `Flush: 15m` in his roster block.
- Wren's conversation is live — if you just restarted the machine, send one
  seat message so there is a conversation to flush.

## 4. The change

`Flush: 15m` is a promise you made in chapter 2 without seeing it kept: a
conversation idle past fifteen minutes is retired. Fifteen minutes is the
right setting and still the wrong demo — you are not going to sit here
watching a clock. The value takes bare seconds or an `s`/`m`/`h`/`d`
suffix, so shrink the window further for one session:

**Replace** `~/ark/silo/ROSTER.md` — in Wren's block, the `Flush:` line
becomes:

```markdown
- **Flush:** 60
```

**Run**

```bash
cd ~/ark/silo
r4t roster check
```

You should see:

```
You: note — Human without an Address (team cannot tell them)
/home/you/ark/silo/ROSTER.md: OK (2 member(s), leader Wren)
```

One rule worth knowing before you rely on this line: `Flush:` only means
something next to `Continue: on` — flush retires a *continuing*
conversation. Set it on a member without `Continue: on` and the lint warns
instead of blocking:

```
warning: Wren: Flush: set but Continue: is off — flush retires a continuing
conversation, so it is ignored here
/home/you/ark/silo/ROSTER.md: OK (2 member(s), leader Wren, 1 warning(s))
```

Harmless, but it means the field is doing nothing. Yours says OK — keep
going.

## 5. Run it

Give Wren a fact to hold, and prove he has it:

**Run**

```bash
r4t seat send --node silo "Hold this fact for later: the supply cache is at grid square K-19. Confirm."
r4t seat inbox --node silo
```

You should see:

```
── from silo:wren (2026-07-29T05:19:47.051352Z)
Supply cache at grid square K-19 recorded. Confirming storage complete.
```

Now leave the team alone for a bit over a minute — the window is 60
seconds of *idle*, measured from Wren's last completed turn. Then run the
idle sweep. `r4t idle` is the janitor pass a running node performs on its
own; invoking it by hand lets you watch it work:

**Run**

```bash
r4t idle --node silo
```

You should see:

```
drained 0 queued turn(s); nudged the leader on 0 quiet thread(s)
pruned 0 stale lock(s); expired 0 thread(s); drained 0 more queued turn(s)
```

The summary doesn't mention the flush — the receipt lives in the team log
and on disk, which is where the next section takes you.

## 6. Expected receipt

**Run**

```bash
r4t logs --node silo -n 6
```

You should see:

```
r4t: FLUSH dump turn -> wren (conversation idle > 60s)
turn: 1 message(s) -> Wren (threads 01ABC..., rig silo)
done: Wren, exit 0 in 28.8s
r4t: ECHO-REPLY wren (rig silo) 674 bytes of cleaned stdout staged as the reply to r4t:silo
r4t: RELEASED silo:wren -> r4t:silo thread=01ABC... hop=1
r4t: FLUSH retired wren's conversation — the next real message refounds it from state on disk
```

Two `FLUSH` events bracket a real turn. The **dump turn** is an ordinary
continuing turn whose prompt asks one thing: "Save your current state and
progress to STATUS.md." It is logged, captured, and budgeted like any
other turn, and only if it exits cleanly is the conversation retired. Look
at what Wren wrote:

**Run**

```bash
cat STATUS.md
```

You should see:

```
# STATUS.md — Wren

## Codeword
TIDEPOOL (confirmed)

## Supplied Facts
- Supply cache location: grid square K-19

## Current State
Active. No open tasks. Waiting for instructions.

## Progress
Session initialized. Stored codeword and supply cache location.
```

Wren chose that structure himself — the dump prompt names the file, not the
format. (Where the file lands depends on the harness: OpenCode anchors
relative paths at the repo root, so on the free path it appears at
`~/ark/silo/STATUS.md`; a harness that stays in Wren's `Workdir:` writes
`agents/wren/STATUS.md` instead. Either way it is inside the team repo,
which matters at the commit point.)

The conversation is now retired. Ask for the fact back:

**Run**

```bash
r4t seat send --node silo "What was the codeword, and where is the supply cache?"
r4t seat inbox --node silo
```

You should see:

```
── from silo:wren (2026-07-29T05:23:14.719675Z)
The codeword was **TIDEPOOL** and the supply cache is located at **grid square K-19**.
```

That turn was a **refound**: no continue flag on the CLI (there is no
conversation left to continue), and the prompt opens with a preamble —
"Check your STATUS.md to refresh your memory." A fresh conversation,
founded from saved state, holding facts learned in a conversation that no
longer exists.

### The rig swap

Flush is scheduled death. A rig swap is sudden death: the conversation is
keyed on the CLI that holds it, so a rig that now drives a *different* CLI
cannot resume it — and r4t does not try, because the old CLI may be
quota-dead and unable to run a dump turn. State on disk is whatever the
last flush left. Swap Wren to the other path's harness (this beat needs a
second continue-capable CLI installed — `agent` here; free-path readers
with only ollama can read along, the machinery is identical):

**Run**

```bash
r4t rig swap silo cursor --model claude-fable-5-medium
r4t seat send --node silo "New rig, same Wren? Tell me the codeword and the cache location."
r4t seat inbox --node silo
```

You should see:

```
swapped rig 'silo' to cursor in /home/you/.config/r4t/rigs.json
  invoke: agent --model claude-fable-5-medium -p --trust --force --approve-mcps {prompt}
── from silo:wren (2026-07-29T05:26:27.150992Z)
Same Wren. Codeword: **TIDEPOOL**. Supply cache: **grid square K-19**. Both verified against STATUS.md.
```

And the log names what happened:

**Run**

```bash
r4t logs --node silo -n 8
```

You should see:

```
r4t: QUEUED silo:you -> wren thread=01ABC... hop=0 "New rig, same Wren? Tell me the codeword and the cache location." (depth 1)
r4t: CONTINUE-SWAP wren (rig silo) drives 'agent' but the conversation lives on 'opencode' — retired; this turn refounds from state on disk
turn: 1 message(s) -> Wren (threads 01ABC..., rig silo)
done: Wren, exit 0 in 12.4s
r4t: ECHO-REPLY wren (rig silo) 102 bytes of cleaned stdout staged as the reply to silo:you
r4t: SEAT you <- silo:wren (parked)
r4t: RELEASED-internal silo:wren -> silo:you thread=01ABC... hop=1
r4t: ANSWERED thread=01ABC... silo:wren -> silo:you (originator answered, thread closed)
```

`CONTINUE-SWAP`: the conversation lived on `opencode`, the rig now drives
`agent`, so the old conversation is retired and this turn refounds — same
mechanics as after a flush, minus the dump. Wren's identity crossed a CLI
boundary on the strength of a markdown file. Swap back:

**Run**

```bash
r4t rig swap silo opencode-ollama --model qwen3.6
r4t seat send --node silo "Back on the local rig. Codeword and cache location, one line."
r4t seat inbox --node silo
```

You should see:

```
swapped rig 'silo' to opencode-ollama in /home/you/.config/r4t/rigs.json
  invoke: ollama launch opencode --model qwen3.6 -- run --auto --dir . {prompt}
── from silo:wren (2026-07-29T05:27:48.329830Z)
Codeword: TIDEPOOL — Cache: grid square K-19
```

The log shows the same `CONTINUE-SWAP` event with the CLIs reversed. One
subtlety the event teaches: a swap that keeps the CLI (a model change, or
`opencode` ↔ `opencode-ollama`, which both drive `opencode`) keeps the
conversation — only a real CLI change retires it.

(If a send comes back with `queued — Wren is resting (member budget ...)`,
you have simply outrun Wren's spend budget with all this demo traffic. The
message is queued, not lost; wait the minutes it names and run
`r4t clear --node silo`. Chapter 4 makes that machinery the main event.)

## 7. Break it

`STATUS.md` looks like a single point of failure, so attack it. Retire the
conversation again — wait a bit over a minute, flush, then delete the
state file before sending the recall:

**Run**

```bash
r4t idle --node silo
rm STATUS.md
r4t seat send --node silo "What was the codeword?"
r4t seat inbox --node silo
```

You should see:

```
── from silo:wren (2026-07-29T05:34:01.509705Z)
Codeword: TIDEPOOL
```

The lobotomy failed. Worth understanding rather than celebrating.

## 8. Diagnose

The refound turn told Wren to check a file that did not exist — so why does
he still know? Because `STATUS.md` was never the only layer. Every turn's
prompt carries a section r4t maintains itself:

```
## Your conversation so far (messages you received and sent)
```

— the member's rolling message history, machinery-kept, capped by the
rig's history budget. The codeword rode in on that. So refound memory is
belt and braces: `STATUS.md` holds what the *member* chose to write down
(judgments, progress, structure), the rolling history holds what was
*said* recently (until it scrolls off the cap). Delete one and the other
covers — for a while. A fact old enough to have scrolled out of history
survives only if Wren wrote it to `STATUS.md`. That is the layer you just
deleted, and on a longer-lived agent the loss would have been real.

## 9. Fix

Nothing is broken in the machinery — what's missing is the file, and the
machinery that wrote it once will write it again. Wait past the window and
run another sweep:

**Run**

```bash
r4t idle --node silo
ls STATUS.md
head -8 STATUS.md
```

You should see:

```
STATUS.md
# STATUS.md — Wren

## Codeword
TIDEPOOL (confirmed)

## Supplied Facts
- Supply cache location: grid square K-19
```

The dump turn regenerated it from the live conversation.

## 10. Check

One full recall-after-flush round trip — the same health check the whole
chapter has been circling:

**Run**

```bash
r4t seat send --node silo "Codeword and cache location, one line."
r4t seat inbox --node silo
```

You should see:

```
── from silo:wren (2026-07-29T05:38:30.929831Z)
Codeword: TIDEPOOL — Cache: grid square K-19
```

Flushed, refounded, swapped, swapped back, state file deleted and
regrown — and Wren still answers. The team is whole.

## 11. Customize

Put the window back where it belongs:

**Replace** `~/ark/silo/ROSTER.md` — Wren's `Flush:` line becomes:

```markdown
- **Flush:** 15m
```

**Run**

```bash
r4t roster check
```

You should see:

```
You: note — Human without an Address (team cannot tell them)
/home/you/ark/silo/ROSTER.md: OK (2 member(s), leader Wren)
```

The window says how long a pause Wren's conversation survives. Fifteen
minutes is a good default: short pauses keep their momentum, and after a
longer one the flush has already banked everything that matters in
`STATUS.md`, so the next message starts fresh without starting over.
Set it late enough that normal working pauses don't cost Wren his
short-term memory, early enough that he isn't dragging yesterday into
today. On paid rigs a short window also keeps resumed context from
billing at full length; the mechanics behind that live in
[Prompt-Cache Economics](https://github.com/neilobremski/bin/wiki/Prompt-Cache-Economics).

## 12. Commit point

`STATUS.md` is Wren's own writing and it lives in the repo — version it.
The snapshot you commit is the state Wren would refound from if the
machine died right now:

**Run**

```bash
cd ~/ark/silo
git add ROSTER.md STATUS.md
git commit -q -m "silo: Wren's first memory dump"
```

Chapter 4 gives Wren something no amount of memory provides: a teammate.
