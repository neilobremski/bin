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
r4t roster check                      # lint roster ↔ harness mappings
# register with a8s (exact commands printed by r4t init)
```

The roster (`ROSTER.md`) names members and symbolic rigs; the harness
config (`~/.config/r4t/rigs.json`) maps those rigs to CLIs. Unknown rigs
fail closed — see the [tutorial](docs/tutorial.md#missing-rig-no-default--fail-closed).

### How a message flows

1. `tell acme:phil "..."` routes through a8s to the team node; the node's
   definition invokes `r4t dispatch`.
2. r4t parses the `[r4t task=<ulid> hop=<n> auto]` header (creating a new
   task if absent), checks every gate below, and runs Phil's rig
   with a prompt carrying his persona, rolling history, and the message.
3. The harness's `$TELL_OUTBOX_DIR` points at a per-turn staging dir, so
   Phil replies with the ordinary `tell` — unmodified. After the turn r4t
   releases the staged envelopes: sender attribution is free (only that
   turn wrote there), the task/hop header + `auto` class mark are stamped
   mechanically (the LLM never sees or copies headers), the send quota and
   suppression checks apply, outbound messages land in Phil's history, and
   the envelopes move into the node's real outbox for a8s.
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
tell acme:gerry "Ship the payload refactor; report when reviewed."
```

Watch it: `a8s logs acme-node -f` (traffic + every governance decision line),
`r4t status --node acme` (locks, buckets, tasks, dead letters), and the
dead-letter dir under `~/.config/r4t/teams/acme/`.

## Chat: the human seat

`r4t chat` (from the team repo, or `--node <team>`) is all of the above in
one window: messages delivered to the roster's human member, turn starts and
completions, governance events, and an input line that sends as that human.
Typed lines go to the leader by default; `/to <member>` retargets, `/who`
shows the roster with live turn locks, `/tasks` the task ledgers, `/quit`
leaves. Training wheels, not a replacement for autonomy — the team runs
through normal dispatch with all governance; chat is how you watch and steer
a young team before letting it run unattended.

Two requirements, both checked at startup with the fix printed: the human
member's `Address:` must be a registered a8s agent whose definition is a
file-proxy (`a8s define <agent> ~/bin/apps/a8s/definitions/human.json`), and
the team namespace must be bound (`a8s namespace <team> <node-agent>`).
Chat spawns a dedicated router for the human seat (taking over from a shared
handler if needed — a shared one delays delivery behind long team turns) and
one for the node if nothing handles it; both stop at `/quit`.

## Governance knobs

All keys live in the out-of-repo rig config (`~/.config/r4t/rigs.json`).
Per-rig keys go inside a rig block; the rest are top-level. Rationale and
prior art per layer: [docs/governance.md](docs/governance.md).

| Key | Default | Governs | Failure mode it stops |
|---|---|---|---|
| `max_sends_per_turn` (rig) | 6 | Envelopes released per turn; excess dead-letters | Runaway fan-out width |
| `max_turns_per_task` (rig) | 25 | Weighted per-task turn budget (1/M per turn); exhaustion forces one leader synthesis turn, then the task closes | Quota burn without work; endless chains |
| `hop_limit` (rig) | 4 | Chain depth; past it the message dead-letters and the originator is told once | Trivial loops (backstop) |
| `timeout_seconds` (rig) | 900 | Harness wall clock; the process group is killed | Hung harnesses |
| `concurrency` (rig) | 1 | Live turns within one rig | Rig-wide pile-ups |
| `throttle.max_concurrent` | 1 | Live turns across ALL rigs | Team-wide pile-ups |
| `throttle.min_seconds_between_turn_starts` | 15 | Cadence floor between turn starts; blocked messages defer to `pending/` and drain later | Invisible burn — a storm degrades into a watchable drip |
| `suppression_window_seconds` | 600 | Content-keyed (sender, recipient, normalized hash) pair suppression window; repeats dead-letter | Ack storms; room ping-pong |
| `bucket_max` / `bucket_earn_ratio` | 8 / 0.1 | Reply-privilege bucket: violations drain 1.0, clean turns earn the ratio; below half the agent's turns stop (inbound still records to history) and recover autonomously | A misbehaving agent muting everyone else's budget |
| `nudge_cap` | 2 | Idle-recovery nudges per agent per task; past it the leader closes the task with what exists | Recovery machinery becoming its own storm |
| `quiet_task_seconds` | 1800 | Termination backstop: an open task with nothing in flight and no ledger activity for this long closes through forced synthesis | A member turn that "succeeds" without replying — no crash, no evidence, task dangles and the originator never hears back |
| `breaker_cap` / `breaker_cooldown_seconds` | 5 / 600 | Failure breaker: after N consecutive failed turns (nonzero exit or timeout) the member's turns pause; one probe turn runs per cooldown until a turn succeeds, and a deliberate (non-`auto`) message resets it. A blocked message dead-letters and its task closes through forced synthesis, so the originator still gets an answer | A broken harness (bad flag, revoked key, dead local model) burning a turn on every inbound message while the asker waits forever |
| `active_ttl_rotations` | 3 | Idle passes an agent stays on the crash-recovery watch list | Unbounded watch lists |
| `rebroadcast_senders` | `["chatroom"]` | Inbound from these is classed bulk; a bulk-triggered turn may post back to that room at most once per suppression window | Two agents looping through a re-broadcasting room |

Class marking ties it together: every envelope r4t releases carries `auto`
in its header, so a header **without** `auto` was written by a deliberate
hand — and resets that task's turn budget (human attention licenses more
work). Suppressed, cut, and excess messages are never silently dropped:
each becomes one JSON record (reason, count, from, to, task, time) in
`~/.config/r4t/teams/<node>/dead-letter/`.

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

Layout: `r4t.py` (CLI) · `dispatch.py` (turns, staging release, forced
synthesis, idle recovery) · `tasks.py` (header + ledger) · `state.py`
(all on-disk state under `$R4T_HOME`) · `harness.py` (rig config) ·
`roster.py` · `sandbox.py` + `sandbox/` (the end-to-end harness).
Observability rides on a8s: traffic in the a8s txlog/convo, r4t decision
lines in the node log via dispatch stdout, r4t-only state via `r4t status`.
