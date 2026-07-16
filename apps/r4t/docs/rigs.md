# Rig configuration reference

Everything that lives in the out-of-repo rig config
(`~/.config/r4t/rigs.json`, relocatable with `R4T_HOME`): presets, model
selection, the settings surface, and the governance knob table. For the
roster side see the [tutorial](tutorial.md); for why each governance layer
exists see [governance.md](governance.md).

## Presets

Rig **names** are yours (`leader`, `member`, `reviewer`, …); **presets** are
CLI templates aligned with [a8s definitions](../../a8s/definitions/):
`claude`, `codex`, `cursor`, `opencode`, `copilot`, `agy`, plus the
`ollama launch`-wrapped local variants (`opencode-ollama`, `claude-ollama`,
`codex-ollama`, `copilot-ollama` — see
[harness-ollama-launch.md](harness-ollama-launch.md)).

```bash
r4t rig presets                       # list presets + invoke lines
r4t rig add worker opencode           # add rig "worker" from a preset
r4t rig add brain agy --model sonnet  # pick a model for the preset
r4t rig swap worker agy               # switch a rig's preset, keep settings
r4t rig remove worker                 # drop a rig (alias: rm)
```

`r4t rig remove <rig>...` (alias `rm`) deletes one or more rigs. It refuses
if a roster member or pin still references the rig, naming what does; pass
`--force` to remove anyway.

## Picking a model (`--model`)

`r4t rig add` and `r4t rig swap` take an optional `--model`. For most presets
(`claude`, `codex`, `cursor`, `opencode`) it is spliced into the invoke at add
time — `--model <alias>` for claude, `-m <id>` after `exec` for codex, after
`run` for opencode — and omitting it lets the CLI's own default apply. The
`ollama` preset and the `ollama launch`-wrapped presets have no default, so
their `--model` is required and names a local model tag.

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
2026-07-14 incident are in [harness-agy.md](harness-agy.md). Like every
other r4t preset, agy is trusted with normal filesystem permissions.

## Editing a rig's settings (`configure` / `set` / `get` / `unset`)

Rig settings never need hand-edited JSON. The configurable keys are
`concurrency`, `rig_budget_max`, `rig_budget_earn_per_hour`, the context knobs
`history_max_bytes` / `history_body_max` / `prompt_body_max`, and `model`
(each detailed in the [knob table](#governance-knobs) below).

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

## The economics: budgets, not cuts

A member runs while its own spend bucket, the shared cell bucket, and (if the
rig declares one) the rig's own bucket all hold ≥1 unit (a turn costs 1 of
each). An empty bucket means the member is *resting* — its queue holds and it
runs again when the bucket refills. Messages are never dropped for lack of
budget.

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

## Governance knobs

Per-rig keys go inside a rig block; the rest are top-level. Governance
defaults apply with no extra configuration — a rig config with only rig
invoke lines is a fully governed team. Rationale and prior art per layer:
[governance.md](governance.md).

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
