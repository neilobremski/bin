# r4t — Roster For Teams

Define a whole team of AI agents in one human-readable `ROSTER.md`; turn any
repo into a governed team on the [a8s](../a8s/README.md) network. One a8s
node per team repo owns a namespace prefix (`acme:*`); an out-of-repo harness
config decides what each roster rig actually runs. Every turn is dispatched,
budgeted, throttled, and audited by r4t — no agent polices itself, and
nothing ever waits on a human.

Why each governance layer exists, with prior art:
[docs/governance.md](docs/governance.md).

**New here?** Step-by-step setup, rigs, and fail-closed behavior:
[docs/tutorial.md](docs/tutorial.md).

## Quick start

```bash
cd /path/to/your/repo
r4t init                              # ROSTER.md + ~/.config/r4t/rigs.json
r4t rig add worker opencode       # when roster members need extra rigs
r4t rig add brain agy --model sonnet   # pick a model for the preset
r4t rig swap worker agy           # switch a rig's preset, keep its settings
r4t rig remove worker             # drop a rig (alias: rm)
r4t roster check                      # lint roster ↔ harness mappings
# register with a8s (exact commands printed by r4t init)
```

The roster (`ROSTER.md`) names members and symbolic rigs; the harness
config (`~/.config/r4t/rigs.json`) maps those rigs to CLIs. Unknown rigs
fail closed — see the [tutorial](docs/tutorial.md#missing-rig-no-default--fail-closed).

### Picking a model (`--model`)

`r4t rig add` and `r4t rig swap` take an optional `--model`. For most presets
(`claude`, `codex`, `cursor`, `opencode`) it is spliced into the invoke at add
time — `--model <alias>` for claude, `-m <id>` after `exec` for codex, after
`run` for opencode — and omitting it lets the CLI's own default apply. The
`ollama` and `opencode-ollama` presets have no default, so their `--model` is
required and names a local model tag.

`agy` is different: its `--model` takes an exact display name from `agy models`
(short aliases are silently ignored), and those names carry version numbers
that change as agy ships releases. So r4t stores the friendly string you give
(`--model sonnet`) and resolves it against the live `agy models` list before
**every** turn — never a pinned table that could go stale. Matching is
case-insensitive with dashes and spaces interchangeable (`gemini-3.5-flash`
matches "Gemini 3.5 Flash (Medium)"); when several names match, the tie-break
prefers the fewest extra tokens, then the highest effort suffix
(thinking > high > medium > low), then alphabetical order. A string that
matches nothing fails the turn loudly with the available names — an unresolved
value is never passed through, because agy would silently run its default.

The `agy` preset runs **without** `--sandbox`. agy's sandbox confines the
agent's child-process writes to the CWD, which blocks `tell` (its staging
outbox lives outside the workplace repo) — the whole capability map and the
2026-07-14 incident are in [docs/harness-agy.md](docs/harness-agy.md). Like
every other r4t preset, agy is trusted with normal filesystem permissions.

`r4t rig remove <rig>...` (alias `rm`) deletes one or more rigs. It refuses if
a roster member or pin still references the rig, naming what does; pass
`--force` to remove anyway.

### Editing a rig's settings (`configure` / `set` / `get` / `unset`)

Rig settings no longer need hand-edited JSON. The configurable keys are
`concurrency`, `rig_budget_max`, `rig_budget_earn_per_hour`, the context knobs
`history_max_bytes` / `history_body_max` / `prompt_body_max`, and `model`
(each detailed in [Governance knobs](#governance-knobs)).

```bash
r4t rig configure specialist          # walk every setting, Enter keeps each
r4t rig set specialist concurrency 2  # write one explicit value
r4t rig get specialist                # list effective settings, source-annotated
r4t rig get specialist concurrency    # one value on stdout (script-friendly)
r4t rig unset specialist concurrency  # drop it back to the default
```

`configure` prompts one key at a time, showing the effective value and its
source in brackets (`history_max_bytes [25000, from preset opencode]:`).
**Plain Enter keeps the current state exactly** — an explicit value stays
explicit and an inherited tier default stays inherited; it is never written
into `rigs.json`, so `rig swap` can still re-resolve the tier. Only typed input
becomes an explicit value. Piped stdin works (one answer per line, EOF keeps
the rest), so an agent can drive it non-interactively.

`get` annotates each value's source: `explicit`, `from preset <name>` (a
context knob inheriting the preset's text tier), or `built-in default`. With a
key it prints the bare value on stdout and the source on stderr, so
`conc=$(r4t rig get specialist concurrency)` captures cleanly.

`model` is special: `set`/`configure` re-resolve the invoke through the rig's
recorded preset, exactly like `rig add --model` (agy keeps its live fuzzy match
per turn). A rig with no recorded preset errors, pointing at
`r4t rig swap <rig> <preset> --model ...`. Raw `invoke` arrays are never
exposed through this surface; use `rig add`/`swap` to change the harness.

### How a message flows

1. `tell acme "..."` routes through a8s to the team node; the node's
   definition invokes `r4t dispatch`. **The topmost leader IS the garden
   from outside** — every external message enters at the top, no matter how
   it is addressed. A `node:member` sub-address from an outside sender is
   ignored (the namespace is the garden's outside address, not a way in to a
   specific member); the leader is the one who decides what to relay inward.
   The lone exception is the roster human's own `Address:` — a reply from it
   is the human speaking, so it lands in the seat path and routes exactly
   like a chat/seat send (see the seat section).
2. r4t opens or continues a thread (a conversation label; a fresh thread
   opens for external mail) and ENQUEUES the message into the leader's
   durable queue — unconditionally. When the leader is runnable (both its
   spend bucket and the cell bucket hold ≥1, its breaker is closed, the
   throttle admits a start), ONE turn drains its whole queue: the prompt
   carries its persona, rolling history, and every waiting message at once.
   Intra-team routing (below) is what delivers to a named member — from
   *inside* the garden, addressing is honored.
3. The harness's `$TELL_OUTBOX_DIR` points at a per-turn staging dir, so
   Phil replies with the ordinary `tell` — unmodified. Inside the walls a
   message is a structured r4t-message, not a text header: dispatch reads the
   staged files as drafts (`to` + `body` + optional files) and stamps the rest
   as fields — `from` (from the staging dir, unforgeable), the thread/hop, and
   a `class` (`human` deliberate · `auto` relay/nudge · `error` feedback). Each
   reply is attributed to the thread of the message it answers, the send quota
   applies, outbound messages land in Phil's history, and the message goes
   straight onto the recipient member's queue (intra-team, no header, no
   round-trip) or is converted to an a8s envelope at the wall (external — the
   only place a wire header exists, carrying `class` as `x_r4t_class`).
   Inside the team, agents address each other by bare first name
   (`tell gerry`) — the namespace prefix is the *outside* address of the
   walled garden, and roster agents never see it. Release canonicalizes
   recipients: bare roster names become intra-team routes, human members
   resolve to their real a8s address, and anything else (`chatroom`,
   external addresses) passes through untouched.
4. Agents never wait for replies in a turn (actor doctrine): delegate, end
   the turn, get woken when replies arrive, answer the originator when
   there is enough. `tell --sync` to teammates is prohibited by prompt and
   pointless by design.
5. Stdout fallback — `tell` always wins. A turn that staged even one
   envelope keeps its stdout as transcript. But a turn that exits 0,
   releases nothing, and printed a non-trivial answer (the classic
   weak-rig shape: small local models reliably answer in text and never
   run `tell`) gets its cleaned stdout — ANSI and harness chrome stripped —
   staged as one reply to the inbound sender, riding every gate in step 3.
   No configuration; strong models never trigger it, and stdout-only
   models participate without knowing the protocol exists.

Governance defaults apply with no extra configuration — a rig config
with only rig invoke lines is a fully governed team.

## Example: a team on a real repo

See [docs/tutorial.md](docs/tutorial.md#example-existing-repo) for the full
sequence. Short version:

```bash
r4t init --root ~/repos/acme
a8s add acme-node ~/repos/acme ~/bin/apps/r4t/example-definition.json
a8s namespace acme acme-node
a8s start acme-node
tell acme "Ship the payload refactor; report when reviewed."
```

Watch it — one surface per way of looking:

- `r4t status` — the snapshot. Leads with plain-English health verdicts
  (waiting on you? runaway? member broken or resting with work queued? team
  budget spent? a queue backing up?), then member budgets, queue depths,
  open threads, and dead letters rolled up by meaning.
- `r4t logs -f` — the stream. The team's own event log: every governance
  decision and turn boundary, including walled-garden traffic that never
  reaches a8s. `--full` includes prompts and transcripts. `--agent <member>`
  narrows the stream to one member; with `--full` it prints that member's
  captured turns (each turn's full prompt and raw output, newest last).
- `r4t chat` — the human, interactively (see the seat section below).
- `r4t seat` — an orchestrating agent, programmatically.

The first dispatch stamps the repo root into team state, so `--node` works
from any directory — and from inside a team repo the `--node` flag itself
is optional. (`a8s logs acme-node -f` still shows the cross-wall view.)

## The seat: being the human in the roster

A human roster member is a first-class team address: teammates just
`tell neil`, and messages park in the node's seat mailbox under
`~/.config/r4t/teams/acme/seat/` — no handler, no router, nothing to
disconnect. When the seat is unattended dispatch rings the `Address:`
doorbell (a copy forwarded over a8s to the human's phone); a reply from that
Address is the human speaking, so it re-enters through the seat path and
routes like a chat send. Outsiders other than the human do not reach the
seat directly — like all external traffic they enter through the top lead.
Two surfaces read and speak for it:

```bash
r4t seat                    # summary: unread count, attached, doorbell
r4t seat inbox              # read parked messages (marks them read; --peek, --json)
r4t seat send "message"     # speak as the human — to the leader
r4t seat send --to phil "…" # or to a member (runs their turn synchronously)
```

`r4t seat` is the scriptable surface — an orchestrating agent impersonates
the human with it directly. `r4t chat` is the human view over the same
mailbox, and the team's **control plane**: a full-screen TUI with a health
header fed by the same verdict engine as `r4t status`, a clickable member
status panel (who is active, resting, or broken and how deep each queue is),
a conversation pane beside a fly-on-the-wall activity pane, and an input line
(`/to`, `/attach`, `/detach`, `/who`, `/threads`, `/help`, `/quit`). The TUI
needs [textual](https://textual.textualize.io/) (`python3 -m pip install
textual`); without it — or with `--plain`, or piped — chat falls back to
a line UI over the same feed. While chat (or anything touching the
presence file) is attached, dispatch skips the `Address:` doorbell;
detach and the doorbell rings again.

**Gemba attach — walk the floor, read-only.** Click a member or type
`/attach vela` (`r4t chat --attach vela` to open straight into it) for a live
view of one member: every message it receives as it is enqueued, and its
turn output streaming as it comes out (teed to `agents/<member>/live.log`,
truncated at each turn start). For the record after the fact, every turn is
also captured whole under `agents/<member>/turns/` — one markdown file per
turn (prompt + raw output, successes and timeouts alike, most recent 50 kept),
surfaced by `r4t logs --agent <member> --full`. Attaching is observation only — it never sends
to that member; the composer keeps talking to the seat's usual
counterparties. `/detach` steps back out. Training wheels, not a replacement
for autonomy — everything still flows through normal dispatch and governance.

## The tree: cells, leads, and information hiding

A team is a tree of small **cells**, not a flat pool of peers. Give each AI
member a `Cell:` line (its cell) and a `Lead:` line (the member it reports
to); the top lead reports to the human:

```markdown
### Cass
- **Status:** AI
- **Rig:** specialist
- **Cell:** design
- **Lead:** Vela
```

Once any `Lead:` line is present the tree becomes structural, not advisory:

- **Information hiding.** A member's turn prompt lists only its tree-adjacent
  names — its lead, its direct reports, its cell-mates — plus the human seat.
  It never sees the whole roster, so lateral contact is not advertised.
- **Delivery follows the `comms` setting** (org-level, default `open`). In
  `open` a tell to any valid roster member delivers — a learned address works
  even though the prompt did not list it (info hiding stays at the prompt
  level, softening the tree tax). In `closed` a tell outside a member's
  adjacency is rerouted to its lead (`[r4t rerouted: Ann -> Cal] …`, logged
  `REROUTED`) — the military model. In both, replies to whoever messaged you
  this turn and anything to the human seat always get through. Set it in
  `r4t-org.json` (below).

`r4t roster check` lints the shape: every `Lead:` must name a real member,
exactly one member is the leader, a cell warns past 6 AI members and errors
past 10, and a tree deeper than 2 levels below the top lead warns (the
span-of-control bounds from the org research). A roster with **no** `Lead:`
lines is a flat team — one cell under the leader — and none of this applies.

Why: in the first live run a full-roster prompt let a build-cell lead message
a design-cell lead laterally *because the name was in front of him*. The tree
held voluntarily, but voluntary is not a control — information hiding removes
the temptation, rerouting removes the option. See
[docs/governance.md](docs/governance.md) §8 for the evidence.

## The mission file

Drop a `MISSION.md` at the repo root and it becomes the team's north star: a
short, **human-owned** page stating *why* the repo exists and what "done"
looks like — purpose, end state, and the current milestone, never the *how*.
It outranks every other document in the repo; where anything conflicts with
it, it wins.

Injection is **leads-only**. Every member with direct reports gets the file
verbatim at the top of each turn prompt, under a section labelled *"The mission
(MISSION.md — outranks every other document)"*. ICs never see it injected —
their lead restates the relevant intent as ordinary messages, at the
resolution the receiver can hold. Intent flows edge-by-edge down the tree,
restated at every hop: "who gets the mission" has the same answer as "who
reports to whom". (A flat roster with no `Lead:` lines treats the marked
leader as the only lead. Any member with tools can of course open the file
itself; there is no machinery for that.)

This follows commander's-intent doctrine (US Army ADP 6-0): intent is the
purpose and desired end state, not a plan, restated down the chain so each
level can act on its own when the plan meets reality. When the file changes —
which should happen only at milestone boundaries — the briefback ritual
applies: the top lead's next turn restates the intent in their own words to
the human and waits for correction before work resumes. That ritual is social
convention, not machinery: r4t injects the file and lints its length, nothing
more. `r4t roster check` warns when `MISSION.md` runs past ~40 lines, because
intent that no longer fits a page has usually gone stale into planning.

## Portable orgs

By default `ROSTER.md` and `MISSION.md` live in the repo — the slow furnace a
proven structure graduates into. When you want to keep the org OUT of the repo
(to A/B two casts against the same project, or to iterate on a roster without
touching the codebase), make an **org directory**: put `ROSTER.md` +
`MISSION.md` there alongside an `r4t-org.json` naming the workplace repo.

```json
{ "repo": "/path/to/the/repo" }
```

Register the a8s node at the org dir; turns run and commit in `repo`, while the
roster, mission, and mission injection read from the org dir. Org-to-repo is
many-to-one: two org dirs (same `MISSION.md`, different `ROSTER.md`) can point
at two clones of one project without their team state colliding — state is
per-a8s-node, not per-repo. `r4t roster check --root <org-dir>` lints the org,
including a malformed `r4t-org.json`, a bad setting value, or a workplace repo
that does not exist. **Graduation is trivial:** copy the two files into the repo
and delete `r4t-org.json` — resolution falls back to the in-repo default with no
other change.

### Org settings

`r4t-org.json` also carries org-level settings that travel with the org, not the
machine. They are optional and may be the *only* thing the file holds — a
config without a `repo` key is an in-repo org that just wants settings.

| Key | Default | Governs |
|---|---|---|
| `comms` | `open` | `open` delivers a tell to any valid member (learned addresses); `closed` reroutes non-adjacent tells through the sender's lead |
| `leader_sees_lateral` | `false` | when `true`, a lateral (peer) delivery lands a read-only copy in the lead's history — no turn burned |
| `egress` | `true` | only the topmost leader may message outside the garden; a non-top member's external tell redirects up to it. `false` keeps the org silent outward |

## Governance knobs

All keys live in the out-of-repo rig config (`~/.config/r4t/rigs.json`).
Per-rig keys go inside a rig block; the rest are top-level. Rationale and
prior art per layer: [docs/governance.md](docs/governance.md).

The economics are budgets, not cuts: a member runs while its own spend
bucket, the shared cell bucket, and (if the rig declares one) the rig's own
bucket all hold ≥1 unit (a turn costs 1 of each). An empty bucket means the
member is *resting* — its queue holds and it runs again when the bucket
refills. Messages are never dropped for lack of budget.

The rig bucket is the quota answer. A rig maps to a real subscription (an
Antigravity plan good for ~20 prompts an hour, a Claude seat), so its ceiling
is set **on the rig** and is **machine-global**: it binds every r4t team on
the machine that shares the rig, so one subscription is safely shared across
projects. Its bucket lives in `~/.config/r4t/rig-buckets.json` (outside any
team) and every node charges it atomically. Budget refill IS the retry: an
exhausted rig rests every member on it, on every team, and the held queues
catch up when it refills — r4t is the retry system so a8s stays dumb delivery.
A subscription can run dry mid-plan without any error: agy/claude/opencode all
exit 0 with a **blank** response when out of quota. So a turn that exits 0,
releases nothing, and prints not one byte is treated as quota-suspect
(`QUOTA-SUSPECT` in the log) and drains the rig bucket, resting the whole rig
until it refills. The rule is deliberately conservative — only a *truly empty*
transcript triggers it, never chrome-only output from a quiet-but-alive member.

| Key | Default | Governs | Failure mode it stops |
|---|---|---|---|
| `budget_max` / `budget_earn_per_hour` (rig) | 8 / 4 | Per-member spend bucket. A turn costs 1 unit regardless of how many queued messages it consumes; empty = resting. Put frontier rigs on a low budget (slow, smart), local rigs on a high one (near-free) | Money burn; a fast rig outrunning its quota |
| `rig_budget_max` / `rig_budget_earn_per_hour` (rig) | unset (no rig gate) | Machine-global rig spend bucket for the subscription behind the rig. A turn also costs 1 rig unit; when empty, every member on that rig rests on every team. Set both together to bind a shared plan (e.g. 20 / 20 for ~20 prompts an hour) | A shared subscription outrunning its real quota across projects |
| `max_sends_per_turn` (rig) | 6 | Envelopes released per turn; excess dead-letters | Runaway fan-out width |
| `history_max_bytes` / `history_body_max` / `prompt_body_max` (rig) | by preset tier — big (agy/codex/claude) 50k/12k/24k · moderate (cursor/opencode/copilot) 25k/6k/12k · small (ollama variants, or no preset) 8192/2000/4000 | Context sizing on the rig: rolling-history budget, per-entry history clip, and per-message prompt clip. `rig add`/`swap` record the preset; explicit values override the tier | A weak rig drowning in context, or a strong one starved of it |
| `timeout_seconds` (rig) | 900 | Harness wall clock; the process group is killed | Hung harnesses |
| `concurrency` (rig) | 1 | Live turns within one rig | Rig-wide pile-ups |
| `cell_budget_max` / `cell_budget_earn_per_hour` | 16 / 8 | Shared cell spend bucket; a turn also costs 1 cell unit. When empty, everyone rests | Whole-cell money burn |
| `throttle.max_concurrent` | 1 | Live turns across ALL rigs | Team-wide pile-ups |
| `throttle.min_seconds_between_turn_starts` | 15 | Cadence floor between turn starts; a member that can't start yet keeps its queue and runs later | Invisible burn — a storm degrades into a watchable drip |
| `quiet_task_seconds` | 1800 | Backstop: an open thread whose originator has not been answered and that has seen no activity for this long wakes the leader with a nudge to report current state | A thread that dangles — a turn "succeeds" without replying and the originator never hears back |
| `breaker_cap` / `breaker_cooldown_seconds` | 5 / 600 | Failure breaker: after N consecutive failed turns (nonzero exit or timeout) the member's turns pause; one probe runs per cooldown until a turn succeeds. Queued messages hold — nothing is dropped | A broken harness (bad flag, revoked key, dead local model) burning turn after turn while messages pile up |

When an org goes fully quiet — every queue empty, no open thread — but the
mission may not be met, the idle pass hands the topmost leader a budget-gated
**mission-review** turn to reweigh the mission and delegate the next step
(cadence is the a8s `idle.timeout` with a widening backoff; three silent
reviews go dormant until a real message or a `MISSION.md` change re-arms it).
The nudge never asks the leader to report to the human. Prompt text — the turn
framing, doctrine bullets, and both nudges — is overridable per key under a
`prompts` object in the a8s node definition (defaults live in `dispatch.py`);
the definition reaches r4t via `--definition $DEFINITION_PATH`.

The durable queue ties it together. Every inbound message to a member
enqueues unconditionally — no gate ever drops or dead-letters a deliverable
message. Dead letters are for *undeliverable* mail only (unknown recipient,
disabled member, a rig that will not resolve) plus a per-turn send-quota
overflow: each becomes one JSON record (reason, count, from, to, thread,
time) in `~/.config/r4t/teams/<node>/dead-letter/`. Duplicate collapse
replaces pair suppression: when the newest queued entry has the same sender
and identical normalized body, the arrival collapses into it with a
`repeats` count rather than adding noise. A turn drains the WHOLE queue at
once (batch invoke): one prompt shows every waiting message, so an agent
pivots on the current state instead of burning a turn per message.

Inside the walls there is no text header: a message is a structured
r4t-message whose fields (`thread` label, telemetry `hop` that never cuts a
message, and a `class` of `human`/`auto`/`error`) travel end to end, stamped by
dispatch and never written or parsed as prose. The only wire header is at
egress, where an external release is converted to an a8s envelope carrying the
bare body and `class` as `x_r4t_class` metadata — other a8s nodes must not need
to know whether a name is one agent, a human, a device, or a whole roster.
Symmetrically, external ingress is untrusted: a sub-address can't pick a member
and nothing is parsed out of the body — everything from outside enters at the
top lead on a fresh thread. One ingress point means one thing to reason about.

## Security model

- **Symbolic rigs.** The in-repo roster may only name a rig
  (`leader`, `member`, ...). Argv, timeouts, and limits live exclusively in
  the out-of-repo config — a repo edit can never change what runs. An
  unknown rig fails closed: that member does not run.
- **Pins.** `"pins": {"gerry": "leader"}` overrides the roster's Harness
  line silently — an in-repo edit can't upgrade a pinned agent.
- **Out-of-repo state.** All r4t state lives under `~/.config/r4t/` (relocate
  with `R4T_HOME`, mirroring `A8S_HOME`); the repo working tree is touched
  only by the harness subprocesses themselves.
- **No shell.** `{prompt}` substitutes into a single argv element; harness
  invocation never goes through a shell.
- **Attribution by filesystem.** Staged envelopes are attributed to the
  turn that owned the staging dir; a8s's router force-stamps `from` by
  outbox ownership. Neither trusts message content.

## Testing

- **Unit + fake sandbox (plumbing):** `r4t sandbox --fake` runs a bundled
  three-agent team (Lead/Dev/Tester building a tiny battleship game)
  against deterministic scripted agents — no LLM calls — inside a
  throwaway `A8S_HOME`/`R4T_HOME`, then emits a self-contained report on
  **stdout** (progress on **stderr**). MECHANICAL CHECKS are computed
  (program built and runs, leader answered the originator, turns within
  budget, zero orphan processes, dead-letter counts). The pytest suite
  runs it end to end.
- **Live sandbox (acceptance / eval):** `r4t sandbox` (no `--fake`) runs
  the same scenario with a real harness. Pick any named preset:
  `r4t sandbox --preset opencode` (default), or local models via Ollama:
  `r4t sandbox --preset opencode-ollama --model qwen2.5-coder:7b`.
  Other presets (`claude`, `codex`, `cursor`, `agy`, …) work the same
  way — see `r4t rig presets`. `live-agent.py` prepends explicit
  per-role steps and stages protocol tells if the model skips them.
  Save the report: `r4t sandbox --preset agy > report.md`

## Development

```bash
python3 -m pytest apps/r4t/tests/     # from anywhere in ~/bin — the repo
                                      # venv wrapper supplies pytest
```

Layout: `r4t.py` (CLI) · `dispatch.py` (enqueue, batch turns, staging
release, quiet-thread sweep, mission-review) · `tasks.py` (thread ledger) · `state.py`
(all on-disk state under `$R4T_HOME`) · `harness.py` (rig config) ·
`roster.py` · `verdict.py` (health verdicts + dead-letter rollup, shared
by status and chat) · `chat.py` (seat feed + line UI) · `chat_tui.py`
(Textual front end) · `sandbox.py` + `sandbox/` (the end-to-end harness).
Observability rides on a8s: traffic in the a8s txlog/convo, r4t decision
lines in the node log via dispatch stdout, r4t-only state via `r4t status`.
