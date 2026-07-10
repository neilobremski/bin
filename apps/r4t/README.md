# r4t — Router For Teams

Turn any repo into a governed team of AI agents on the [a8s](../a8s/README.md)
network. One a8s node per team repo owns a namespace prefix (`s1l:*`); a
human-readable `ROSTER.md` in the repo names the members; an out-of-repo
harness config decides what each symbolic tier actually runs. Every turn is
dispatched, budgeted, throttled, and audited by r4t — no agent polices
itself, and nothing ever waits on a human.

Why each governance layer exists, with prior art:
[docs/governance.md](docs/governance.md).

## Quickstart (any repo)

```bash
cd /path/to/your/repo
r4t init
```

`r4t init` writes a starter `ROSTER.md` (a Human owner, an AI `Lead` on the
`leader` tier, a `Dev` on the `member` tier) if the repo has none, writes
`~/.r4t/harnesses.json` from built-in defaults if you have none (both tiers
run `opencode run --auto`; the `_notes` key documents swapping in `claude`,
`agent`, `agy`, ...), and prints the exact registration sequence:

```bash
a8s add myrepo-node /path/to/your/repo ~/bin/apps/r4t/example-definition.json
a8s namespace myrepo myrepo-node
a8s start myrepo-node
tell myrepo-node "hello"       # bare node -> the roster leader
tell myrepo:dev "hello"        # namespace -> a specific member
```

Edit the roster (names, personas, tiers), run `r4t roster check`, and go.
A config with nothing but tiers gets every protection below at its default —
zero governance configuration is a fully governed team.

### How a message flows

1. `tell s1l:phil "..."` routes through a8s to the team node; the node's
   definition invokes `r4t dispatch`.
2. r4t parses the `[r4t task=<ulid> hop=<n> auto]` header (creating a new
   task if absent), checks every gate below, and runs Phil's tier harness
   with a prompt carrying his persona, rolling history, and the message.
3. The harness's `$TELL_OUTBOX_DIR` points at a per-turn staging dir, so
   Phil replies with the ordinary `tell` — unmodified. After the turn r4t
   releases the staged envelopes: sender attribution is free (only that
   turn wrote there), the task/hop header + `auto` class mark are stamped
   mechanically (the LLM never sees or copies headers), the send quota and
   suppression checks apply, outbound messages land in Phil's history, and
   the envelopes move into the node's real outbox for a8s.
4. Agents never wait for replies in a turn (actor doctrine): delegate, end
   the turn, get woken when replies arrive, answer the originator when
   there is enough. `tell --sync` to teammates is prohibited by prompt and
   pointless by design.

## Example: the s1l team

```bash
r4t init --root ~/repos/s1l           # keeps the existing ROSTER.md if present
a8s add s1l-node ~/repos/s1l ~/bin/apps/r4t/example-definition.json
a8s namespace s1l s1l-node
a8s start s1l-node
tell s1l:gerry "Ship the ECS payload refactor; report when reviewed."
```

Watch it: `a8s logs s1l-node -f` (traffic + every governance decision line),
`r4t status --node s1l` (locks, buckets, tasks, dead letters), and the
dead-letter dir under `~/.r4t/teams/s1l/`.

## Governance knobs

All keys live in the out-of-repo harness config (`~/.r4t/harnesses.json`).
Per-tier keys go inside a tier block; the rest are top-level. Rationale and
prior art per layer: [docs/governance.md](docs/governance.md).

| Key | Default | Governs | Failure mode it stops |
|---|---|---|---|
| `max_sends_per_turn` (tier) | 6 | Envelopes released per turn; excess dead-letters | Runaway fan-out width |
| `max_turns_per_task` (tier) | 25 | Weighted per-task turn budget (1/M per turn); exhaustion forces one leader synthesis turn, then the task closes | Quota burn without work; endless chains |
| `hop_limit` (tier) | 4 | Chain depth; past it the message dead-letters and the originator is told once | Trivial loops (backstop) |
| `timeout_seconds` (tier) | 900 | Harness wall clock; the process group is killed | Hung harnesses |
| `concurrency` (tier) | 1 | Live turns within one tier | Tier-wide pile-ups |
| `throttle.max_concurrent` | 1 | Live turns across ALL tiers | Team-wide pile-ups |
| `throttle.min_seconds_between_turn_starts` | 15 | Cadence floor between turn starts; blocked messages defer to `pending/` and drain later | Invisible burn — a storm degrades into a watchable drip |
| `suppression_window_seconds` | 600 | Content-keyed (sender, recipient, normalized hash) pair suppression window; repeats dead-letter | Ack storms; room ping-pong |
| `bucket_max` / `bucket_earn_ratio` | 8 / 0.1 | Reply-privilege bucket: violations drain 1.0, clean turns earn the ratio; below half the agent's turns stop (inbound still records to history) and recover autonomously | A misbehaving agent muting everyone else's budget |
| `nudge_cap` | 2 | Idle-recovery nudges per agent per task; past it the leader closes the task with what exists | Recovery machinery becoming its own storm |
| `active_ttl_rotations` | 3 | Idle passes an agent stays on the crash-recovery watch list | Unbounded watch lists |
| `rebroadcast_senders` | `["chatroom"]` | Inbound from these is classed bulk; a bulk-triggered turn may post back to that room at most once per suppression window | Two agents looping through a re-broadcasting room |

Class marking ties it together: every envelope r4t releases carries `auto`
in its header, so a header **without** `auto` was written by a deliberate
hand — and resets that task's turn budget (human attention licenses more
work). Suppressed, cut, and excess messages are never silently dropped:
each becomes one JSON record (reason, count, from, to, task, time) in
`~/.r4t/teams/<node>/dead-letter/`.

## Security model

- **Symbolic tiers.** The in-repo roster may only name a tier
  (`leader`, `member`, ...). Argv, timeouts, and limits live exclusively in
  the out-of-repo config — a repo edit can never change what runs. An
  unknown tier fails closed: that member does not run.
- **Pins.** `"pins": {"gerry": "leader"}` overrides the roster's Harness
  line silently — an in-repo edit can't upgrade a pinned agent.
- **Out-of-repo state.** All r4t state lives under `~/.r4t/` (relocate
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
  throwaway `A8S_HOME`/`R4T_HOME`, then writes a self-contained report
  whose MECHANICAL CHECKS section is computed (program built and runs,
  leader answered the originator, turns within budget, zero orphan
  processes, dead-letter counts). The pytest suite runs it end to end.
- **Live sandbox (acceptance / eval):** `r4t sandbox` (no `--fake`) runs
  the same scenario with the real harnesses from
  [sandbox/harnesses.json](sandbox/harnesses.json) under tight limits.
  Use it to grade prompt and governance changes against real models; it is
  never run from pytest. The report (default `./r4t-sandbox-report.md`) is
  written for an external judge — mechanical checks first, then the turn
  table, full conversation, governance events, and produced source.

## Development

```bash
python3 -m pytest apps/r4t/tests/     # from anywhere in ~/bin — the repo
                                      # venv wrapper supplies pytest
```

Layout: `r4t.py` (CLI) · `dispatch.py` (turns, staging release, forced
synthesis, idle recovery) · `tasks.py` (header + ledger) · `state.py`
(all on-disk state under `$R4T_HOME`) · `harness.py` (tier config) ·
`roster.py` · `sandbox.py` + `sandbox/` (the end-to-end harness).
Observability rides on a8s: traffic in the a8s txlog/convo, r4t decision
lines in the node log via dispatch stdout, r4t-only state via `r4t status`.
