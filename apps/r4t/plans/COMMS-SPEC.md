# Comms re-founding — implementation spec

*2026-07-14. Decisions locked with Neil in the rulings on #192, #185, #189,
#190, #160, #183 (all dated 2026-07-14) and motivated by the n5a
[RETRO.md](../experiments/n5a/RETRO.md). Six issues, ONE implementation PR,
spec first (this doc). Style and rigor follow
[history/CELL-SPEC.md](history/CELL-SPEC.md), the record of the re-founding this
one builds on. This is the active spec; every open question is resolved inline
below (marked DECIDED).*

*Scope: `neilobremski/bin`, `apps/r4t/` (plus one small enabling change in
`apps/a8s/`). Pre-v1 scorch-the-earth throughout — no migration code, no
on-disk compat; a running org restarts after merge.*

---

## 1. Background and the through-line

The n5a retro lit up two failure axes (RETRO Findings 3–6): the tree is
gate-clean but **slow and stall-prone** (10 REROUTED events, an org that slept
2h of politeness), the flat org is fast but **constraint-blind**. Every issue
in this batch is a knob that lets an operator sit anywhere on the speed↔gates
axis instead of forking the code — the meta-principle Neil set on #185:
**every this-or-that becomes a setting with a proposed default and a declared
home.** The one exception that is not a setting is #192, which is a
representation change underneath all of them.

The through-line: intra-garden comms today are a8s `tell` envelopes carrying a
text header (`[r4t task=<ulid> hop=<n> auto]`) that r4t stamps on release and
parses on ingress. That header round-trip is the source of an entire class of
bugs (#160), cannot carry structured metadata (#167), and makes the reroute /
comms-doctrine logic (#185, #183) awkward to build. #192 replaces it with a
structured message r4t owns end-to-end; the settings then attach cleanly to the
new release path.

---

## 2. The internal message protocol (#192) — DECIDED

### 2.1 What existed before (the header round-trip)

Trace one intra-team message under the old scheme:

1. Agent runs `tell <name> "<body>"`. `tell` atomic-writes an a8s envelope
   (`{id, to, content, files}`) into the per-turn staging dir
   (`$TELL_OUTBOX_DIR`, owned by exactly this turn — attribution for free).
2. `release_staging` read each staged envelope, `parse_header`'d its `content`
   (agents never write a header, so this always yielded `body` verbatim),
   applied quota / reroute / thread-attribution, then for an internal
   recipient called `_release_one`, which **serialized** the resolved
   `(thread_id, next_hop, auto)` back into a text header and handed
   `f"{header} {body}"` to `_ingest(..., trusted_header=True)`.
3. `_ingest` **re-parsed** that header to recover `(thread_id, hop, auto, body)`
   and enqueued a structured dict `{from, to, task, hop, auto, body}`.

Step 2→3 was a serialize/deserialize with no purpose but to reuse `_ingest`.
The queue entry was *already* structured. The header text was real only on the
a8s **wire** (external egress/ingress) and in that pointless internal
round-trip.

### 2.2 The r4t-message schema

One structured object, canonical on the member queue and in staging. Renames
`task`→`thread` and replaces the `auto` bool with a `class` enum:

```json
{
  "id":        "<ulid>",              // message identity (dedup, gemba preview)
  "from":      "<node>:<member>",     // STAMPED by the system, never claimed
  "to":        "<node>:<member>",     // canonical recipient
  "thread":    "<ulid>",             // conversation label (was "task")
  "hop":       0,                      // telemetry only; never cuts a message
  "class":     "human|auto|error",   // message class (was the `auto` bool)
  "body":      "<text>",             // the message; the ONLY thing agents write
  "repeats":   1,                      // duplicate-collapse count
  "queued_at": "<iso8601>",
  "files":     []                      // attachment refs (intra-team dropped today)
}
```

- **`from` is stamped, not claimed.** The filesystem is the unforgeable
  identity (same principle as `mailbox.py` force-overwriting `from`): a staging
  dir belongs to exactly one member's turn, so `release_staging` stamps
  `from = {node}:{member}`. Agents write only `to`, `body`, and optional
  `files` — they cannot spoof a sender.
- **`class`** subsumes the old `auto` mark (RFC-3834 analog) and carries #167's
  metadata natively: `human` = a deliberate hand (seat / doorbell / external
  ingress), `auto` = machine-generated or a relay/nudge (internal release,
  quiet nudge, mission-review, lateral copy), `error` = operational feedback
  (see #160, §2.6). Class is set by the code path, never written by an agent.
- **`thread`** is the conversation label (unchanged semantics from `task`):
  attribution, answer-the-originator closure, quiet-thread sweep. Never gates
  delivery.

### 2.3 On-disk queue representation

Unchanged layout, new schema:
`teams/<node>/agents/<member>/queue/<time_ns>-<seq>.json` — one r4t-message per
file, filename monotonic in arrival order. Staging
(`agents/<member>/staging/*.json`) holds the agent's **emitted drafts**: `to` +
`body` + optional `files` only; `from`, `thread`, `hop`, `class` are stamped at
release. No text header appears on disk anywhere inside the walls.

### 2.4 How members emit — KEEP `tell`

The agent contract does not change — `tell <name> "<body>"`, whatever is most
natural for an LLM CLI agent. What changed is entirely internal: dispatch reads
staged files as **r4t-message drafts** and stamps the structured fields, rather
than serializing them into a header. `tell` writing to `$TELL_OUTBOX_DIR` is a
staging representation, not "an a8s tell envelope on the wire" — the thing #192
retires is the wire header, and it is gone. (Rejected: a structured-stdout
convention reintroduces the parsing failure class #192 removes; a new `r4t say`
verb is `tell` with worse muscle memory and no egress reuse.)

### 2.5 Egress / ingress — the only conversions, at the wall

Mirror of PR #179's one-way-in/one-way-out:

- **Egress (r4t-message → a8s envelope), at release for an external `to`:**
  `content = body`, `x_r4t_class = class`, `from = <node/namespace>` (the a8s
  router force-overwrites `from` to the node on the wire, so outside nodes see
  one a8s node, never "deep" — §4). The r4t header never leaves the garden;
  `thread`/`hop` stay garden-internal.
- **Ingress (a8s envelope → r4t-message), at the top only:** external mail
  enters at the topmost leader (PR #179). A fresh `thread` is minted, `hop=0`,
  `class` defaults to `human`. No header is parsed. Reading `x_r4t_class` off an
  inbound external envelope is #167's ingress half and needs a8s to expose
  envelope extras to the wake; **out of scope for this batch** (§9). The roster
  human's `Address:` doorbell reply is re-stamped to the seat as `class=human`.

Internal delivery no longer converts anything: `release_staging` resolves
`(thread, hop, class)` and enqueues the r4t-message directly onto the recipient
member's queue. The `format_header`+`_ingest` internal round-trip and the
`trusted_header` / `parse_header` path are deleted.

### 2.6 #160 is structurally eliminated — DECIDED

The headerless `_tell_error` turn was a header-parsing symptom, and #192
removes its cause. Under the internal protocol there is no outbox round-trip for
intra-team feedback: an operational error to an **intra-team** sender becomes an
**internal r4t-message stamped `class=error`, carrying the ORIGINATING thread
id** (#160 Option 1), enqueued directly. It cannot mint a new thread (it *has*
the thread), so it can never spawn the headerless-new-task family; it dies at
the existing budget/answer gates like any other message. **External** senders
(human / off-team) keep the direct a8s tell — the useful case. A member turn
rendering a `class=error` message sees it flagged as an operational error line,
not a fresh directive. Asserted with a regression test (§8, Phase 1).

---

## 3. Comms doctrine as an org setting (#185) — DECIDED

Two org-level settings, homed in `r4t-org.json` (see §7).

### 3.1 `comms: open | closed`  (default `open`)

- **`closed`** — the military model: hard reroute-through-lead. An intra-team
  `tell` to a member that is not tree-adjacent (and did not message the sender
  this turn) reroutes to the sender's lead with a note. This is the exact
  `release_staging` reroute block guarded by `_reachable_names`.
- **`open`** (new default) — the "learned address" civilian model: a message
  addressed to **any valid roster member delivers**, no reroute. Info hiding
  stays **at the prompt level only**: `_teammate_lines` still lists just
  tree-adjacent names (a learned address comes from being messaged or
  introduced), but a `tell` to a valid non-adjacent member is *delivered*, not
  bounced. This decouples "what the prompt advertises" from "what delivers,"
  directly softening the tree tax (RETRO Finding 4).

Implementation: `open` disables the reroute block in `release_staging`
(unknown-name mail still dead-letters as before; only the *valid-but-not-
adjacent* case changes from reroute to deliver). `_teammate_lines` is unchanged
in both modes.

### 3.2 `leader_sees_lateral: false | true`  (default `false`) — DECIDED

Leader visibility into lateral (peer-to-peer) traffic. OFF: leaders do not see
lateral messages in-band; the team daily log + gemba already record everything
for the operator (no change). ON: a lateral delivery also lands a **read-only
history copy** on the sender's lead — a `class=auto` entry appended to the
lead's conversation history, **never enqueued**, so the lead sees it on its next
real turn without a turn being spent to notify. Traffic UP to the lead is
skipped (already visible). Orthogonal to `comms`. (Rejected: a queued CC copy,
which would burn a leader turn per lateral message — exactly the storm the tree
was built to damp.)

---

## 4. Topmost-leader egress as an org setting (#183) — DECIDED

Org-level `egress: true | false`  (default `true`), homed in `r4t-org.json`.

- The org presents as a **single a8s node**; egress stamps `from = <node>` on
  the wire so outside nodes can't tell it is deep (§2.5).
- **Only the topmost leader** may originate external mail. When `egress = true`
  and the emitting member is the top leader, an external `to` releases to the
  real outbox (converted per §2.5). When `egress = false`, no member — not even
  the top leader — may message outside; the top leader's external `to`
  dead-letters with an audit note.
- A **non-top** member's external `to` redirects to the top leader regardless
  of `comms` mode and of `egress` — externally the org *is* the top leader
  (RETRO/SYNTHESIS doctrine, now mechanical). "How the leader responds stays up
  to the prompt/agent" (#183 ruling).

Implementation: in `release_staging`, the external-`to` branch gains the
top-leader + `egress` gate before `_release_one`'s outbox path.

---

## 5. Budget-gated mission-review idle turn (#189) — DECIDED

### 5.1 The gap

`run_idle` was janitorial: quiet-thread sweep + drain. An org whose threads
have all closed with the mission unmet slept forever — nothing reopened it
(RETRO Finding 3; quill slept ~2h). The furnace must burn on its own.

### 5.2 The stall condition (what r4t CAN detect)

Fire a mission-review turn only when the org is **structurally stalled**:

- every member queue empty (`members_with_queue(node)` is empty), AND
- no OPEN threads (`tasks.list_tasks` has no `status=open`), AND
- the drain pass this idle tick ran nothing (no live work), AND
- no live turn is in flight.

### 5.3 "Milestone unmet" — resolved without mechanized tracking

#189 says "milestone unmet," but CELL-SPEC Phase 3 **deliberately does not
mechanize milestone status** ("milestones stay prose; the human interprets
them"). r4t therefore cannot know the mission is unmet. Resolution: r4t detects
the *structural* stall (§5.2) and hands the **top leader** a mission-review turn
whose prompt asks the leader to judge whether the mission is met — *the leader
interprets milestone-met, not r4t*. Quiescence is natural: a leader that judges
the mission done stages nothing, the backoff (§5.5) widens, and termination
after K silent reviews (§5.5) ends the loop.

### 5.4 Budget gate + precedence

- **Budget-gated:** before firing, run `_runnable` for the top leader (member
  AND cell AND rig buckets). If resting, **skip** and hold the counter at the
  threshold so the review fires the moment the bucket refills (#189: "expensive
  leads a non-issue by construction; a broke agent doesn't run").
- **Real messages take precedence:** the mission-review only fires into a
  genuinely empty leader queue. `run_idle` drains first; the review is attempted
  only after a no-op drain with empty queues and no open threads.

### 5.5 Cadence = `definition.idle.timeout` + r4t backoff — DECIDED

a8s already calls `r4t idle` every `definition.idle.timeout` seconds (0
disables) — that is the base tick; r4t reads nothing for cadence. r4t backs off
*on top*: a confirmed-stall counter in team state fires the review on the ~2nd
consecutive stalled tick, then widens (2→4→8… ticks, capped at 32) so a stalled
org pings its leader a few times an hour, not every tick. Any real turn resets
the counter.

**Termination (DECIDED):** after K consecutive mission-review turns that stage
nothing (K=3), treat the mission as leader-judged-done and go **dormant** until
a **real message or a MISSION.md change** re-arms it. "Staged nothing" is
observed directly: after the review turn runs, if no member queue is non-empty
and no thread is open, the review was silent. State:
`teams/<node>/mission-review.json` (`{stalls, silent_reviews, dormant,
mission_mtime}`).

### 5.6 The nudge prompt

A default prompt (`prompts.mission_review`, overridable — §6.1) injecting the
mission context and asking the leader to review progress and decide the next
move. **It reiterates that no communication to the human NEEDS to happen**
(#189): a mission-review is not a status-report request and must not train
leaders to doorbell the human every cycle. Delivered as a `class=auto` internal
message to the top leader from `r4t:<node>`.

---

## 6. Prompt overrides + history-size knob (#190) — DECIDED

### 6.1 Prompt overrides via the a8s node definition — DECIDED

Defaults stay in `dispatch.py` (`PROMPT_DEFAULTS`); the node definition provides
**sparse per-key** overrides under a `prompts` object (scorch-the-earth on
shape, no compat). Per-bullet keys (not a coarse block) — sparse override is the
whole point, and retuning one doctrine line must not restate the other six.
Enumerated keys, extracted from `build_prompt` and the nudges:

| Key | What it is |
|---|---|
| `prompts.intro` | "You are {name}, a member of the {node} team, working in {workplace}… relative paths only." |
| `prompts.mission_header` | "## The mission (MISSION.md — outranks every other document)" |
| `prompts.work_batch` | "This is one turn: you were woken with every message above at once…" |
| `prompts.work_never_wait` | "Never wait for a reply inside a turn…" |
| `prompts.work_tell` | "Send messages with the `tell` shell command…" (+ reply/teammate sub-lines) |
| `prompts.work_direct` | "Speak to teammates directly and one at a time…" |
| `prompts.work_no_ack` | "Do not send acknowledgment-only messages…" |
| `prompts.work_body_only` | "Your tell's body is the only thing the recipient sees…" |
| `prompts.work_commit` | "Repo work is not done until it is committed." |
| `prompts.quiet_nudge` | the `_quiet_task_sweep` body |
| `prompts.mission_review` | the §5.6 nudge |

Structural section headers ("## Who you are", "## Your conversation so far",
"## Messages since your last turn") stay in code — not doctrine, low value to
override. `{name}`, `{node}`, `{workplace}`, `{creator}`, `{thread}` are the
substitution fields available in override text. A missing definition or missing
`prompts` object yields all defaults.

### 6.2 The `$DEFINITION_PATH` enabler (small a8s change) — DECIDED

r4t needs its node definition path to read `prompts`. A `$DEFINITION_PATH`
substitution is added to `apps/a8s/definitions.py:build_command` (and the idle /
batch builders), alongside `$SENDER` / `$RECIPIENT` / `$MESSAGE` / `$A8S_DIR` —
a8s already knows the path from the registry (`resolve_definition_path`).
`example-definition.json` threads `--definition $DEFINITION_PATH` into the
`dispatch` and `idle` invokes. `DispatchContext` gains `definition_path`; a
small loader reads `prompts` (and tolerates absence → all defaults). (Rejected:
r4t re-reading the a8s registry itself, which couples r4t to a8s's on-disk
state that egress/ingress was built to avoid.)

### 6.3 History size as a rig-level knob

`HISTORY_MAX_BYTES` (state.py), `HISTORY_BODY_MAX` and `PROMPT_BODY_MAX`
(dispatch.py) become per-rig knobs, following the model/budget rig pattern
(`rig.py`) — a 0.6B local member and an agy seat should not share a history
budget. Defaults are **per preset**, by text tier (Neil's PR-review ruling):

| Tier | Presets | `history_max_bytes` | `history_body_max` | `prompt_body_max` |
|---|---|---|---|---|
| big | agy, codex, claude | 50,000 | 12,000 | 24,000 |
| moderate | cursor, opencode, copilot | 25,000 | 6,000 | 12,000 |
| small | opencode-ollama, ollama | 8,192 | 2,000 | 4,000 |

Resolution order: explicit value in rigs.json → the rig's preset's tier → the
small tier. `r4t rig add`/`swap` record a `preset` key on the rig entry; a swap
to a different preset re-resolves the tier while explicit values still win. A
rig with no `preset` (custom/scripted CLI) gets the small tier — conservative
is correct for an unknown harness. Copilot's moderate placement is a judgment
call (Neil's tiers did not name it), flagged on the PR for veto. The knobs
replace `state.HISTORY_MAX_BYTES` / `dispatch.HISTORY_BODY_MAX` /
`dispatch.PROMPT_BODY_MAX`; the global constants are deleted
(scorch-the-earth) and dispatch passes the rig values at write time.

---

## 7. Consolidated settings table

| Setting | Values / type | Default | Home | Issue |
|---|---|---|---|---|
| `comms` | `open` \| `closed` | `open` | `r4t-org.json` (org) | #185 |
| `leader_sees_lateral` | bool | `false` | `r4t-org.json` (org) | #185 |
| `egress` | bool (top-leader external) | `true` | `r4t-org.json` (org) | #183 |
| `history_max_bytes` | int | preset tier: 50k big / 25k moderate / 8192 small | rig (rigs.json, per-rig) | #190 |
| `history_body_max` | int | preset tier: 12k / 6k / 2000 | rig (rigs.json, per-rig) | #190 |
| `prompt_body_max` | int | preset tier: 24k / 12k / 4000 | rig (rigs.json, per-rig) | #190 |
| `prompts.<key>` (11 keys) | text, sparse | code default | a8s node definition | #190 |
| message `class` | `human` \| `auto` \| `error` | per-path | protocol (not user-set) | #192 / #167 |
| `definition.idle.timeout` | seconds | (existing) | a8s node definition | #189 (cadence base) |
| mission-review backoff | derived | 2→widening, K=3-silent stop | team state | #189 |

`r4t-org.json` may exist **without a `repo` key** — purely to carry org settings
(the in-repo default workplace still applies). Homes follow the meta-principle:
org-level → `r4t-org.json`; member-capability → rig; prompt text → a8s
definition.

---

## 8. Implementation phases

Ordered so the tree is green after each phase. **Protocol first**, not settings
first: #185/#183 reshape the exact `release_staging` reroute/egress code that
#192 rewrites, so building settings on the old header round-trip then rewriting
under them would double the work. #190 is orthogonal but placed after so
`build_prompt` is touched once; #189 is the liveness capstone and goes last.

### Phase 1 — internal message protocol (#192 + #160)

New r4t-message schema (§2.2); staging drafts + queue entries carry structured
fields, no text header inside the walls; `release_staging` enqueues internal
deliveries directly; egress/ingress conversion isolated at the wall (§2.5);
`parse_header`/`format_header`/`normalize_content`/`HEADER_RE` deleted; intra-team
`_tell_error` becomes a `class=error` internal message on the originating thread
(§2.6, fixes #160). Rename `task`→`thread`, `auto`→`class` on disk (queue,
velocity.csv, dead-letter records) and in prose/UI.

### Phase 2 — comms + egress org settings (#185 + #183)

`org.py` parses an optional `r4t-org.json` without `repo` and reads `comms`,
`leader_sees_lateral`, `egress` (with `check_org` validating enums/bools).
`release_staging` reroute block becomes `comms`-gated (default `open` = no
reroute for valid members); external-`to` branch becomes top-leader +
`egress`-gated (§4); `leader_sees_lateral` adds the read-only lead copy (§3.2).
`DispatchContext` carries the org settings. **Note the default flip:** `comms`
defaults to `open`, so a default org stops rerouting (RETRO Finding 4).

### Phase 3 — prompt overrides + history rig knobs (#190)

Add `$DEFINITION_PATH` to `apps/a8s/definitions.py` and thread it through
`example-definition.json`; `DispatchContext` gains `definition_path`;
`build_prompt` and the nudges resolve each `prompts.<key>` (default → override).
Add `history_max_bytes` / `history_body_max` / `prompt_body_max` to `Rig` +
`_parse_rig`; delete the three global constants; dispatch passes the rig values.

### Phase 4 — mission-review idle turn (#189)

Extend `run_idle`: after a no-op drain with empty queues and no open threads,
run the stall backoff (§5.5); when it fires and the top leader is `_runnable`,
enqueue the `prompts.mission_review` nudge (`class=auto`), run the leader's turn
to observe whether it delegates, and let the rest drain. Backoff/termination
state in `mission-review.json`.

Each phase leaves `pytest apps/a8s/tests/` and the r4t suite green before the
next begins; the PR is the four phases squashed.

---

## 9. Out of scope (adjacent, not in this batch)

- **#167 external class ingress** — reading `x_r4t_class` off an inbound
  external envelope needs a8s to expose envelope extras to the wake. This batch
  stamps class on egress and carries it internally; external inbound defaults to
  `class=human`. Tracked in #167.
- **Materialize MISSION.md into the workplace** (RETRO Finding 1) — a separate
  follow-up; both n5a orgs wrote shadow missions, arguing for it, but it is not
  one of the six issues here.
- **a8s attachment dead-letter** (RETRO Finding 7) — a8s core, unrelated.

---

## 10. Plans-directory cleanup

Executed in this PR: `plans/history/` created; `CELL-SPEC.md` and `departments/`
moved there (both SHIPPED — Phases 1–3, rig budgets #177/#182, portable org
#180/#182, gemba #179 all landed; the four-scenario exploration concluded in
SYNTHESIS). `SYNTHESIS.md` (the living rationale, still cited by
`d5n-notebook.md`), `experiments/d5n-notebook.md` (d5n is active, mid-M4), and
`research/` (ORG-LESSONS still drives `roster.py:tree_problems`) stay in place.
This COMMS-SPEC is the active spec. (n5a lives under `experiments/n5a/`, outside
`plans/`, untouched.)

---

## 11. Operational note

**d5n currently runs off the working tree.** #192 changes the on-disk queue
format (schema rename `task`→`thread`, `auto`→`class`; no text header), `comms`
flips the default to `open`, and history knobs move to the rig — all pre-v1
scorch-the-earth with no migration. **Any live org (d5n included) must be
restarted after this PR merges**: drain or accept in-flight queue loss, then
re-kick. Because the change is a working-tree code change, the moment the PR
merges the running dispatcher picks up the new code against the old on-disk
schema — so the merge should be paired with a deliberate restart, not left to
drift. Per the standing experiment-freeze rule, do not merge while a live
experiment is mid-measurement; land it between runs.
