# CLAUDE.md — onboarding for Claude Code (and humans) working in `~/bin/`

This file captures conventions, decisions, and gotchas accumulated across the
development of this repo. Read it before making changes. Update it when you
learn something a future contributor would have wanted to know.

## Repo shape

`~/bin/` is a personal utilities repo plus one substantive sub-project (`apps/a8s/`).

- **Top level** — small single-file CLIs (`tell`, `aztail`, `speak`, `ltx-video`,
  `h`, `NMP.py`, `py-json-tool`, etc.). Each is independently usable. `install.sh`
  adds the dir to `$PATH` and links docs/skills.
- **`apps/a8s/`** — Agent Infinity System. Multi-module Python project, ~2200
  LOC across 8 modules, full pytest suite. The bulk of recent work.
- **`docs/`** — markdown for each top-level command + symlinks for skill install.
- **`venv/`** — local Python virtualenv (gitignored). Pytest is installed there.
  Run `python3 -m pytest ...` from anywhere; it picks up `venv` automatically
  via the wrapper at `bin/python3`.

## Conventions

### Shebangs

All bash scripts use `#!/usr/bin/env bash` (not `#!/bin/bash`). macOS ships
bash 3.2.57; users with Homebrew bash get a modern version this way. Don't
introduce `#!/bin/bash`.

### Polyglot bash + PowerShell scripts

Cross-platform CLIs (`a8s`, `tell`) are polyglots — the same file is valid
bash AND PowerShell. The bash side `exec`s into Python; the PowerShell side
finds `python3`/`python`/`py` via `Get-Command`. The pattern uses
`echo \`# <#` >/dev/null` as a no-op for bash that opens a PowerShell
multi-line comment. Don't add new polyglots without reading an existing one
(e.g., `~/bin/tell`) first.

### Install hook

`install.sh` is sourced from a shell rc. It adds `~/bin/` to `$PATH` AND
auto-links each `docs/<name>.md` into `~/.claude/skills/` if Claude Code is
detected. Adding a new top-level CLI: write the script, write `docs/<name>.md`,
the next shell session gets it as a Claude skill.

### Workflow

**Issues + feature branches off `main`. No direct commits to `main`.** Every
change goes through a PR. The user squash-merges fast. After a squash, rebase
follow-up work onto fresh `main` rather than stacking — squash hashes don't
match the original branch's commits and stacking causes conflicts.

Recovering from "PR conflicts after the previous PR squash-merged":

```bash
git checkout main && git pull --ff-only
git branch -D <stale-branch> 2>&1 || true   # if it was already merged via squash
git checkout -b <branch>-rebased main
git cherry-pick <last-good-commit-from-stale-branch>
git push --force-with-lease origin <branch>
```

Then update the PR's branch on the GitHub side.

### Pre-v1 / scorch-the-earth (a8s only)

`a8s` is explicitly pre-v1. **Do not write migration code.** When the schema
changes, the user wipes `~/.a8s/` and re-derives state via `a8s discover` +
`a8s add`. This applies to registry shape, mailbox layout, definition schema,
and on-disk pid/log paths. The contract changes only when the user declares v1.

Ask before adding any "if old field, infer new field" fallback. The right
answer is almost always "drop the old, error on it." This is a stated user
preference, not a hunch — see the merged history of phases 2 / 3a / 3b / 4 / 5.

### Commit style

- Commits prefixed `feat(a8s)` / `fix(a8s)` / `refactor(a8s)` / `test(a8s)` /
  `docs(a8s)` per Conventional Commits. The `(a8s)` scope appears for a8s
  changes; smaller top-level scripts use `feat(<script>)` etc.
- Body explains the *why* and the design decision, not just the mechanical
  *what*. The locked-design discussion in #52 was preserved across phases by
  including the decision rationale in commit bodies.
- Co-author trailer for AI-assisted work:
  `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`

### Code style

- Default to no comments. Names should explain what; comments are only for
  *why* something non-obvious is done. The user explicitly dislikes
  comment-noise.
- Avoid emojis in source unless asked.
- Don't add abstractions that aren't being used today. Three similar lines is
  fine; abstract on the fourth.
- Don't add error handling for cases that can't happen. Trust internal
  guarantees; validate at boundaries (CLI input, external APIs, filesystem).
- Don't add backwards-compat hacks. See pre-v1 above.

## a8s sub-project

### What it is

Filesystem-based message router that lets independent CLI sessions
(Claude Code, Gemini CLI, Codex CLI, future humans/scripts) talk to each
other as a team. The README in `apps/a8s/README.md` is the design overview;
read it first.

Key invariants:

- **Recipient opacity (strict, mailing-list style)** — sender doesn't know
  whether the recipient is a Claude session, a script, or a human; recipient
  doesn't know who else got the message. A direct tell and an alias-fanned
  tell produce identical message shapes — only `to` differs (alias vs agent
  name). Never re-introduce `alias` / `others_count` fields or a separate
  alias verb. (Settled in issues #69 / #70.)
- **Members don't know about a8s** — drop in any project unchanged.
  An agent just sees a `tell` shell command and wakes to messages.
- **The filesystem is the IPC, the outbox location is the unforgeable identity.**
  Routing force-overwrites the `from` field to the enclosing agent — agents
  cannot spoof. Don't bypass this.
- **One agent, one handler at a time. One handler can serve many agents.**
  pid file at `~/.a8s/agents/<NAME>/pid` is the attachment; multiple agents
  can point at the same PID (multi-agent handler via alias).

### Module map

| File | What's in it | Entry points other modules import |
|---|---|---|
| `apps/a8s/a8s.py` | thin entry shim (~30 lines) | `cli.main` invocation only |
| `apps/a8s/core.py` | paths, logging, helpers, `Participant`, mutable `PRINT_LOCK`, path constants (`SCRIPT_DIR`, `DEFINITIONS_DIR`, `ENTRYPOINT`) | leaf module — no a8s imports |
| `apps/a8s/registry.py` | `~/.a8s/a8s.json` I/O, `resolve_name` (alias resolution with diamond/cycle detection), `sender_from_cwd`, `_scan_for_markers` | depends on `core` |
| `apps/a8s/mailbox.py` | `route_outboxes` (two-phase: ingest → process), `ensure_mailboxes`, `_queue_prompt`, `_queue_clear_sentinel`, `_split_content_and_files`, `_write_outbox`. Per-message `.retry` sidecars live in `pending/` and drive backoff retries via `BACKOFF_SCHEDULE`. | depends on `core`, `registry`, `ulid` |
| `apps/a8s/definitions.py` | `select_verb`, `build_command`, `_expand_argv` (`$SENDER`/`$RECIPIENT`/`$MESSAGE`/`$A8S_DIR`), `load_definition`, `_autodiscover_definition` | depends on `core`, `registry` |
| `apps/a8s/ulid.py` | `new()` / `parse()` / `is_ulid()` — pure-stdlib Crockford-base32 ULIDs. Mailbox messages are named `<ulid>.json` and carry the same id in the body's `id` field for receive-side dedup. | leaf module |
| `apps/a8s/network.py` | `~/.a8s/network.json` IO, `load_remotes`, `make_publish_remotes` (warn-and-continue per-remote publish), `receive_envelope` (decode → ULID dedup → registry filter → atomic inbox write), `start_remotes` / `stop_remotes`. paho-mqtt imported lazily. | depends on `core`, `registry`, `transports`, `ulid` |
| `apps/a8s/transports/__init__.py` | `Transport` ABC + `TransportError`. The publish/subscribe/start/stop contract every mesh transport implements. | leaf module |
| `apps/a8s/transports/mqtt_paho.py` | `PahoMqttTransport` — paho-mqtt with `clean_session=False`, QoS 1, hash-derived stable `client_id` so the broker recognizes the same persistent session across restarts. | depends on `transports`, `paho-mqtt` (soft) |
| `apps/a8s/daemon.py` | `acquire`/`release` (pid files), `attached_loop` (also spawns subscriber threads via `start_remotes` and stops them on detach), signal handling (`_make_signal_handler`, `_kill_wake_subprocess_group`), `run_with_prefix`, `wake_once`. Mutable globals `_STOP_EVENT`/`_SIGNAL_COUNT`/`_CURRENT_WAKE_PROC` live here. Sets `core.PRINT_LOCK`. | depends on everything above |
| `apps/a8s/commands.py` | every `cmd_*`, including `cmd_remote add/remove/ls`, `_install_skill_*` for Claude/Gemini/Codex, `_expand_to_agents` | depends on everything above |
| `apps/a8s/cli.py` | `COMMANDS` table (the source of truth for help text), `dispatch`, `main` | depends on `commands` only |

### Hard constraints when refactoring

- **`cmd_start` re-execs via `core.ENTRYPOINT`**, not `__file__`. After the
  modular split, `__file__` inside `commands.py` resolves to the wrong path.
  `core.ENTRYPOINT = SCRIPT_DIR / "a8s.py"` is the canonical re-exec target.
- **Argv interpolation** (`$SENDER`, `$RECIPIENT`, `$MESSAGE`, `$TIMESTAMP`,
  `$AGE`, `$A8S_DIR`) expands via `definitions._expand_argv`. `$TIMESTAMP`
  is the ISO date the message was queued; `$AGE` is the human-readable
  delta from now (computed each wake, so backlogs get accurate per-message
  ages). `invokeClear` always gets empty strings for the message-shaped
  vars. Don't introduce more placeholders without checking they make
  sense across all three `invoke*` verbs.
- **`core.PRINT_LOCK` is the cross-module log lock.** It's `None` at module
  load and only set when `daemon.attached_loop` starts. Threading is intentional:
  multi-agent handlers may interleave wake events. If you write a new code path
  that calls `core.out_agent` from a new thread, make sure attached_loop is
  the one running.
- **`run_with_prefix` uses `start_new_session=True`** so SIGKILL targets the
  whole subprocess group (claude/gemini/codex CLI plus any helpers it spawned).
  Don't drop this — it's the only way the second-signal kill path works.
- **Per-agent take-over via detach-request (no orphans).** When `acquire`
  hits a live conflict, it writes `~/.a8s/agents/<NAME>/detach-request` with
  its pid and polls. The holder's `attached_loop` checks for that file at
  the top of each iteration and releases just that one agent — its sibling
  attachments stay running. Don't reintroduce process-level SIGTERM-and-wait
  in `acquire`: that's what created the orphan-collateral bug. The 60s
  `DETACH_TIMEOUT_S` is the only fallback if the holder is mid-wake on a
  slow LLM call; `a8s kill` breaks the deadlock.
- **Per-agent kill via kill-request + SIGUSR1.** `cmd_kill` writes
  `~/.a8s/agents/<NAME>/kill-request` and SIGUSR1s the holder. The handler's
  `_on_kill_signal` checks the kill-request for `_CURRENT_WAKE_NAME` (the
  agent being woken right now); if present, it kills the wake subprocess
  group so `run_with_prefix.wait()` returns immediately. The actual release
  of the agent happens at the next iteration top via the kill-request check
  — same shape as detach-request, but logs as "killed by" and takes
  precedence. Whole-process SIGTERM is the last-resort escalation only
  when the holder doesn't honor the request within 10s.
- **Agent-directory invariant — `.outbox/` is one-way.** a8s never reads a
  file in `<root>/.outbox/` for read-modify-write, never writes a sidecar
  there. `route_outboxes` phase 1 atomically renames every new outbox file
  into `~/.a8s/agents/<sender>/pending/<ulid>.json`; phase 2 parses, routes,
  retries, and trashes from there. Cross-fs fallback uses `shutil.copy2 +
  unlink`; ULID-keyed receive-side dedup tolerates the rare interruption
  window. Don't reintroduce in-place outbox writes (sidecars, `from`
  rewrites on disk, etc.) — the agent's directory belongs to the agent.
- **Mesh routing is layered + a8s-opaque.** `network.py` knows the
  publish/subscribe contract; transports under `transports/` implement it.
  Senders publish to all configured remotes; receivers dedupe by ULID. A
  message to an unknown-locally recipient publishes to all remotes (broadcast
  + filter). File payloads (`FILE:`) are local-only in v1 — `route_outboxes`
  marks all configured remotes as "succeeded" for messages with files so
  they finalize after local delivery instead of looping retries.
- **Mesh persistent sessions.** paho-mqtt is configured with
  `clean_session=False` + QoS 1, with a stable hash-derived `client_id`. The
  broker holds messages for an offline subscriber until reconnect — that's
  how a Cloud-Shell-style listener catches up after coming online.
- **Per-message backoff retry.** When a remote publish fails, the
  `<file>.json.retry` sidecar tracks attempts and `next_attempt`. The
  schedule is `BACKOFF_SCHEDULE` (30s → 1m → 2m → 5m → 15m → 30m → 1h → 6h
  → 24h). After `MAX_ATTEMPTS` failures the message moves to trash with a
  "discarded after backoff exhausted" log. Don't introduce per-pass
  unconditional retries — they generate excess log noise and broker traffic.

### Surface

```
add <name> <dir> [<def>]    register an agent (auto-detects definition from marker)
remove <name>                unregister an agent; wipes its mailbox dir and prunes aliases. Refuses if a handler is running.
agents                       list all registered
discover <path>              read-only scan; suggests add+define commands
define <name> [<path>]       show or set definition
alias <alias> <member>       add to alias (creates if new); cycles rejected
unalias <alias> [<member>]   remove member or whole alias
aliases                      list aliases + resolved members
start <name>                 detached background handler (alias = ONE process for N agents)
run <name>                   foreground handler
step <name>                  attach, one route+drain pass, release
stop <name>                  SIGTERM the handler (graceful; alias dedupes by PID)
kill <name>                  etiquette-then-force: SIGTERM, grace, 2nd SIGTERM, SIGKILL
exit                         SIGTERM every running handler
ls                           list only running agents + their handler PIDs
prompt <name> <message>      senderless supervisor message (raw delivery)
tell <name> <message>        routed message (sender = agent enclosing CWD)
clear <name>                 queue CLEAR sentinel (write-time + read-time inbox wipe)
logs <name>... [--tail N] [-f]   merge-sorted per-agent logs
remote add <name> <broker> <topic> [--user U --pass P]   register a paho-mqtt mesh remote
remote remove <name>         forget a remote
remote ls                    list configured remotes
install                      install canonical skills
```

`a8s` no-args prints help. There is no auto-discovery of agents from CWD.

### State on disk

```
~/.a8s/
├── a8s.json                  registry: { agents: {...}, aliases: {...} }
├── network.json              configured mesh remotes (absent → no mesh)
├── seen-ids                  cluster-wide ULID ring (receive-side dedup)
├── log.txt                   process-scoped supervisor log
└── agents/
    └── <NAME>/
        ├── inbox/            JSON messages waiting for wake_once
        ├── inbox.tmp/        atomic-stage dir for fan-out
        ├── pending/          messages a8s has ingested out of <root>/.outbox/
        │                     awaiting full delivery; <ulid>.json plus
        │                     optional <ulid>.json.retry sidecar tracking
        │                     attempts and per-remote success
        ├── trash/             processed / discarded messages
        ├── log.txt            agent-scoped log
        └── pid                handler attachment (one or more agents may share a PID)

<agent-root>/
└── .outbox/                  agent writes here; a8s renames out — never
                              read-modify-writes — to ~/.a8s/agents/<NAME>/pending/
```

### The three invoke verbs

`select_verb(msg)` picks one based on the message's shape:

| Verb | Trigger | Argv vars typically used |
|---|---|---|
| `invokePrompt` | `from` is empty (queued by `a8s prompt`) | `$MESSAGE` (and optionally `$AGE`/`$TIMESTAMP`) |
| `invokeMessage` | `from` set | `$SENDER`, `$RECIPIENT`, `$AGE` or `$TIMESTAMP`, `$MESSAGE` |
| `invokeClear` | `clear: true` field set | none — argv is literal |

There's no separate alias verb. Strict opacity (#69, #70) makes a direct
tell and an alias-fanned tell indistinguishable in shape; `$RECIPIENT`
preserves whatever the sender wrote (alias name for fanned, agent name for
direct) — mailing-list semantics.

`build_command(definition, msg, verb)` reads the matching `invoke*` argv
and substitutes `$SENDER` / `$RECIPIENT` / `$MESSAGE` (content + any
`FILE:` lines) / `$A8S_DIR`. There is no `$PROMPT` and no separate
`promptMessage` template — argv interpolation does the whole job.

### Definition fallback

Every agent always has a definition. If the registry has no `definition` field,
`load_definition` falls back to `apps/a8s/definitions/default.json`, which
runs `apps/a8s/dummy-cli` (a bash script that prints "no real CLI configured"
and echoes the prompt). Wakes never crash on missing config.

`a8s add` auto-detects:
- single marker file in dir → matching `<kind>.json`
- multiple/no markers → `default.json` with a note in the output

### Per-tool quirks

- **Claude Code** — granular permissions via `--permission-mode dontAsk`
  + `--allowedTools "Bash(tell:*) Read Edit Write ..."`. `--continue` for
  conversation continuity. `--dangerously-skip-permissions` for unrestricted
  (no longer baked into a8s; create a custom definition if you want it).
- **Gemini CLI** — `--yolo` is REQUIRED in headless mode. The Policy Engine
  TOML files at `~/.gemini/policies/*.toml` don't apply to non-interactive
  `-p` mode (tracked upstream as `google-gemini/gemini-cli#20469`). Don't
  remove `--yolo` until that issue is resolved.
- **Codex CLI** — `--full-auto` for workspace-write sandbox. `resume --last`
  for continuity. `--skip-git-repo-check` to allow running outside a git
  repo. `stdin=subprocess.DEVNULL` is REQUIRED — codex hangs otherwise
  (learned the hard way; see `daemon.run_with_prefix`).

### SKILL.md YAML — quoted scalars only

Codex's YAML parser is strict and fails silently on unquoted descriptions
containing colons or `FILE:` lines. Always quote `name:` and `description:`
in skill frontmatter. Tested with `apps/a8s/skills/tell/SKILL.md` — unquoted
values silently dropped the skill on codex.

### Testing

```bash
python3 -m pytest apps/a8s/tests/
```

~230 tests, runs in <3s. Mesh tests against a real `mosquitto` broker
(spawned on a free port) skip cleanly when mosquitto or paho-mqtt isn't
installed; install via `pip install -r apps/a8s/tests/requirements.txt`.
Test scaffold:

- `apps/a8s/tests/conftest.py` — adds `apps/a8s/` to sys.path, provides
  `fake_home` fixture that monkey-patches `HOME` to a tmp dir so tests
  never touch the real `~/.a8s/`. Resets `core.PRINT_LOCK` between tests.
- `apps/a8s/tests/fixtures/mock-cli` — deterministic bash echo script for
  end-to-end daemon tests. Each argv element printed on its own line with
  `MOCK-CLI:` prefix; tests grep the per-agent log to assert what the wake
  subprocess actually received.
- `apps/a8s/tests/fixtures/mock.json` — definition that routes all three
  verbs through `mock-cli` with a deterministic argv template
  `FROM:$SENDER|TO:$RECIPIENT|MSG:$MESSAGE` so log assertions are stable.

When you change behavior in `core` / `registry` / `mailbox` / `definitions` /
`daemon`, add or modify the corresponding `test_*.py`. The pytest suite is
fast enough to be the first feedback loop.

## Active design threads

The locked-design refactor (#52) is closed. The following are open:

| # | State | Topic |
|---|---|---|
| #39 | open enhancement | Copilot CLI as 4th tool kind. Trivial after the refactor: write `copilot.json`, add `COPILOT.md` → `copilot` to `core.MARKER_FILES`. |
| #63 | partially landed | Transparent multi-cluster routing. paho-mqtt transport + layered remotes + per-message backoff + ULID dedup are in. Still open: mini-MQTT pure-stdlib fallback (auto-activates when paho isn't importable), HTTPS long-poll transport, peer-to-peer TCP transport, app-level envelope encryption (per-network PSK), cross-cluster `FILE:` payloads (rides #62). |
| #62 | open enhancement | Cross-cluster file payload host (TempFile.org-style ephemeral storage with signed URLs and per-message symmetric keys). v1 mesh strips `files` from incoming envelopes and skips remote publish for outgoing messages with files. |
| #72 | open question | Design discussion surfaced by the review panel: mailbox file format. (#67's atomic fan-out and #63's ULID + ingest-to-pending split partially address this; revisit before mini-MQTT lands.) |

### What I tried that didn't work (concrete)

- **Synchronous `a8s prompt`** — initial implementation woke the agent
  directly; raced with the loop. Fixed in #45 by queueing into the inbox
  with empty `from` and letting the normal drain pass handle it.
- **Auto-discovery on every `step`** — early phase 2 still walked the
  filesystem each iteration; performance was fine but the model leaked
  (an agent dir mid-creation got picked up incomplete). Replaced with
  registry-only iteration in phase 2.
- **Mailboxes inside agent dirs (`<root>/.inbox/`, `<root>/.trash/`)** —
  initial layout. Gemini agents would `ls` their own directory and surface
  the inbox to the model. Moved to `~/.a8s/agents/<NAME>/{inbox,trash}/`
  in phase 3a. Outbox stayed at `<root>/.outbox/` because codex's
  `--full-auto` sandbox can only write inside the workspace.
- **Single `--continue` argv path** — initial Claude wake used
  `claude --resume <session-id> -p "..."` which requires explicit IDs.
  Switched to `--continue` (resumes most recent). Don't try to use
  `--resume` without a session ID; it errors on parse.
- **Headless tool-use without auto-approval** — Gemini and Claude both
  silently deny tools in headless `-p` mode without explicit flags. Symptom:
  the wake hangs indefinitely or returns empty. `--yolo` (gemini),
  `--permission-mode dontAsk --allowedTools "..."` (claude) are required.
- **`a8s loop` as a singleton** — earlier design had one daemon process
  scanning all agents. Replaced in phase 3b with per-agent (or per-alias)
  handlers. The singleton had no path to remote (#63).
- **The `says` broadcast verb** — phase 4 retired it. LLMs struggled to
  pick `tell` vs `says` consistently; the dual API confused agents. Now
  group sends use `tell <alias>` and the system fans out.
- **The `fresh` flag** (`~/.cache/a8s/fresh.json`) — phase 5 retired it
  along with the `consume_fresh` mechanism. Replaced with the explicit
  CLEAR sentinel that `a8s clear` queues into the inbox.
- **`--unrestricted` global flag** — phase 5 retired. Users wanting the
  dangerous mode create a custom definition file and `a8s define <name>
  <path>`. Don't add the flag back; the definition system subsumes it.

### Top-level scripts: `tell`

`~/bin/tell` is the polyglot shim that agents use to send messages. It
execs into `apps/a8s/a8s.py tell ...`. There used to be a `says` polyglot
too; it's gone. If you're considering re-adding any "broadcast" command,
read phase-4's PR description (#58) first — the consensus was that aliases
subsume it and the dual-verb API confuses LLMs.

### When using subagents (Agent tool) for review

The "review panel" exercise (5 simulated reviewers — Carmack, Spolsky, Pike,
Armstrong, DJB) was high-signal. To repeat it for future design decisions:

1. Pick reviewers whose published work directly maps to the design space.
   For a8s the picks were: low-level pragmatism (Carmack), DX/surface (Spolsky),
   namespace abstractions (Pike), distributed messaging (Armstrong), filesystem
   security (DJB). Avoid overlap.
2. Per-reviewer prompts: anchor in their published positions, not caricature.
   Specify exact files to read, focus areas to prioritize, length cap (~500
   words). Different reviewers get different focus areas to avoid redundant
   feedback.
3. Synthesize convergent findings (multiple reviewers same diagnosis = strong
   signal) separately from unique findings (one reviewer's specific catch
   = often a real bug). DJB's case-collision and pid race were unique and
   real.

The synthesis from this exercise lives in issues #65–72.

## Common operations cheat-sheet

```bash
# Start fresh after a schema change (pre-v1 scorch-the-earth)
rm -rf ~/.a8s/agents/ ~/.a8s/a8s.json
a8s discover apps/a8s/tests/agents
# (paste the suggested add commands)

# Run the test suite
python3 -m pytest apps/a8s/tests/

# Smoke-test wake without burning real LLM tokens — stub run_with_prefix
python3 -c "
import sys; sys.path.insert(0, 'apps/a8s')
import daemon
captured = []
daemon.run_with_prefix = lambda n, c, w: captured.append(c) or 0
daemon.attached_loop(['CLAUDE'], 1.0, single_pass=True)
print(captured)
"

# Tail the supervisor log (process-scoped events)
tail -f ~/.a8s/log.txt

# Tail per-agent activity
a8s logs CLAUDE GEMINI -f
```

## Memory note

The user has a private memory system at
`~/.claude/projects/-Users-neilo-bin/memory/` — that's separate from this
file. Personal preferences, ongoing project state, and feedback rules live
there. THIS file (`CLAUDE.md`) is the public-checked-in onboarding doc.
Don't put anything in it that would be inappropriate for a public repo.

The two intersect: anything in private memory that constrains how a8s is
designed (e.g., "no back-compat shims pre-v1") is duplicated here as a
project rule, because contributors who don't share the memory still need
to know it.
