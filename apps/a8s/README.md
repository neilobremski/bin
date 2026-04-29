# a8s — Agent Infinity System

A lightweight way to wire multiple agents — Claude Code sessions, Gemini CLI projects, codex sessions, plain scripts, eventually humans — into a team that can talk to each other.

> **Status: pre-v1.** Surface and storage layout will keep changing without migration paths until the design settles.

## Why

Modern agent tooling like Claude Code's subagents is great inside one process and one tool's permission model. But:

- **Process and machine boundaries matter.** One agent might need codex's workspace-write sandbox; another might need Claude with a narrow allowlist; another might need to run on a different machine entirely. Cramming them into a single host process is the wrong abstraction.
- **Members shouldn't have to know about a8s.** Drop in any existing project unchanged. The agent just sees a `tell` command and wakes to messages — same shape whether it's a Claude session, a Python program, or (someday) an SMS gateway routing to a human.
- **Recipient opacity is the load-bearing invariant.** The sender doesn't know whether the recipient is a Claude session, a script, or a person on the other end of an email-to-message bridge. That's how this scales — anywhere the abstraction fits, you plug in.
- **Eventually, one fabric across machines.** Tracked in #63: two a8s clusters on the same network see each other and route messages as peers. The local design today is shaped to accommodate that without breaking.

The win at scale: a team of agents that share knowledge through ordinary conversation grows faster than a collection of silos, and you interact with all of them through one verb (`tell`).

## Mental model

```mermaid
flowchart LR
    subgraph A["Agent's own dir &nbsp;<i>(~/projects/foo/)</i>"]
        direction TB
        marker["CLAUDE.md / GEMINI.md / CODEX.md<br/><i>marker file</i>"]
        outbox[".outbox/<br/><i>agent writes here</i>"]
    end

    subgraph H["~/.a8s/ &nbsp;<i>(a8s-managed state)</i>"]
        direction TB
        reg["a8s.json<br/><i>registry — agents + aliases</i>"]
        slog["log.txt<br/><i>process-scoped supervisor log</i>"]
        subgraph AG["agents/&lt;NAME&gt;/"]
            direction TB
            ib["inbox/"]
            tr["trash/"]
            alog["log.txt"]
            pid["pid"]
        end
    end

    handler(("handler<br/>process"))

    outbox ==>|"route_outboxes"| ib
    ib ==>|"wake_once<br/>(after subprocess returns)"| tr
    pid -.-|"holds attachment"| handler
    handler -.-|"writes"| alog
```

Three concepts:

- **Registry** (`~/.a8s/a8s.json`) — the list of agents and aliases. Agents have a name, a directory, and a *definition* (a JSON file describing how to wake them).
- **Handlers** — a process that holds the attachment for one or more agents. Pid file at `~/.a8s/agents/<NAME>/pid`. One agent is handled by exactly one process at a time, but one process can handle many agents (typically by attaching to an alias).
- **Mailboxes** — agents write to `<agent-root>/.outbox/`; routing copies into `~/.a8s/agents/<RECIPIENT>/inbox/`; the handler drains the inbox by waking the agent's CLI. Routing is process-agnostic — only waking requires the handler attachment.

The router doesn't trust the sender. The `from` field is force-overwritten to the actual enclosing agent at routing time. An agent can't impersonate another by hand-writing JSON.

## Quickstart

```bash
# Find candidate agents.
a8s discover ~/projects

# Register them. Auto-detects the right definition from the marker file
# (CLAUDE.md / GEMINI.md / CODEX.md).
a8s add CLAUDE ~/projects/code-review
a8s add GEMINI ~/projects/research

# Optional: group them.
a8s alias devs CLAUDE
a8s alias devs GEMINI

# Background daemon handling both members of the alias in one process.
a8s start devs

# See what's running.
a8s ls
#   CLAUDE  PID 12345  /Users/me/projects/code-review
#   GEMINI  PID 12345  /Users/me/projects/research

# Send messages. From anywhere, you can `a8s tell` directly.
# From inside an agent's own root, the agent can use the `tell` skill.
cd ~/projects/code-review
tell GEMINI "look at lines 40-80 of foo.py"
tell devs   "stand-up at 3pm"

# Read what each agent is doing.
a8s logs CLAUDE GEMINI --tail 20

# Stop the daemon (graceful — finishes the current wake first).
a8s stop devs
```

That's the full loop. Members don't know they're "in a8s" — they just see a `tell` command available in their shell and wake to messages the same way they wake to any prompt.

## Commands

### Registration
| | |
|---|---|
| `a8s add <name> <dir> [<def>]` | Register an agent. Auto-detects definition from `<dir>`'s marker file unless `<def>` is given. |
| `a8s remove <name>` | Unregister an agent. Wipes `~/.a8s/agents/<NAME>/` and prunes the agent from any alias's member list (deletes empty aliases). Refuses if a handler is running. |
| `a8s define <name> [<path>]` | Show or set the agent's definition file. |
| `a8s discover <path>` | Walk a path for marker files; print suggested `add`+`define` commands. Read-only. |
| `a8s agents` | List every registered agent and its definition. |

### Aliases
| | |
|---|---|
| `a8s alias <alias> <member>` | Create or extend an alias. Members can be agents or other aliases (cycles rejected). |
| `a8s unalias <alias> [<member>]` | Remove a single member, or the whole alias. |
| `a8s aliases` | List every alias and its resolved members. |

### Handlers
| | |
|---|---|
| `a8s start <name>` | Spawn a detached background process to handle the agent (or every member of an alias, in one process). |
| `a8s run <name>` | Foreground attached loop. Aliases produce one process with interleaved output. Ctrl+C: graceful detach. 2nd Ctrl+C: kill the wake subprocess group. |
| `a8s step <name>` | Attach, do one route+drain pass, release. Heavyweight: detaches the current handler if any. |
| `a8s stop <name>` | SIGTERM the handler. Aliases dedupe by PID — one signal per multi-agent handler. Graceful detach. |
| `a8s kill <name>` | Per-agent force-detach: writes a kill-request, SIGUSR1s the holder. Holder kills the in-flight wake subprocess iff it's for that agent and releases the attachment; siblings keep running. Falls back to whole-process SIGTERM only if the holder doesn't honor the request in 10s. |
| `a8s exit` | SIGTERM every running handler. |
| `a8s ls` | List only running agents and their handler PIDs. |

### Messaging
| | |
|---|---|
| `a8s tell <name> <msg>` | Routed message. `<name>` may be an agent or alias (fans out at routing time). Sender = agent enclosing CWD; `tell` from outside any agent root errors. There is no senderless channel — every message has a force-stamped agent `from`. |
| `a8s logs <name>... [--tail N] [-f]` | Read per-agent log files; merge-sort by ISO timestamp across multiple agents. `-f` follows. |

### Skills
| | |
|---|---|
| `a8s install` | Install canonical skills (`tell`) into Claude / Gemini / Codex user scope. |

### Remotes (issue #63)
| | |
|---|---|
| `a8s remote` | List configured remotes (transport, broker, topic, opts; passwords masked). |
| `a8s remote <name>` | Show one remote's spec. |
| `a8s remote <name> <broker-url> <topic> [--<opt> <value> ...]` | Register or overwrite a remote. Broker URL is `mqtt://host[:1883]` or `mqtts://host[:8883]`. Persistent session + QoS 1 are wired automatically so an offline cluster catches up on reconnect. Any `--<opt> <value>` past the broker and topic is forwarded verbatim to the transport — common ones are `--user U --pass P`, `--client_id ID`, `--keepalive N`. The transport rejects unknown options at load time so typos fail loud. |
| `a8s unremote <name>` | Forget a remote. Running daemons keep using the prior config until restart. |

Remotes are git-shaped: an explicit list of places to fan messages out to. a8s only crosses cluster boundaries on `tell` / `prompt` — everything else (`a8s logs`, `a8s ls`, `a8s agents`) is strictly local. If you want cross-cluster log access, register an a8s connector that turns inbound tells into local `a8s logs` calls; a8s itself just enables the message + invocation path.

Configure as many remotes as you want and a8s publishes to all of them in parallel; receivers dedupe by ULID, so adding redundant brokers improves delivery without producing duplicate inbox writes. A message to an unknown-locally recipient publishes to all configured remotes and is delivered by whichever cluster has the recipient registered locally. Per-message exponential backoff (30s → 1m → 2m → 5m → 15m → 30m → 1h → 6h → 24h) retries unreachable remotes; after the schedule is exhausted the message is moved to the sender's trash with a "discarded after backoff" log.

File payloads (`FILE:`) are local-only in v1 — the sender's path doesn't exist on the receiving cluster. Cross-cluster file transfer rides issue #62.

`a8s` with no command prints help. There is no auto-discovery of agents from CWD — registration is always explicit.

### Per-agent take-over

`start`/`run`/`step` against an agent that's already attached to another live process performs a **per-agent** hand-off. The new caller drops a `detach-request` file under `~/.a8s/agents/<NAME>/`; the existing handler reads it at the top of its next iteration and releases just that one agent — its other handled agents keep running. Then the new caller atomically claims the pid file. There is never an orphan: at every moment, an agent is either attached to exactly one live process or it isn't running at all.

Concretely: P1 is `a8s start devs` (handling `[CLAUDE, GEMINI, FOO]`). You run `a8s run CLAUDE` in another window. CLAUDE moves to your foreground process; P1 keeps handling `[GEMINI, FOO]`. If you then `a8s run GEMINI` in a third window, GEMINI moves there; P1 keeps `[FOO]`. If P1's last agent gets pulled out, P1 exits cleanly with nothing left to handle.

`a8s kill <name>` works the same way but force: it writes a `kill-request` file and SIGUSR1s the holder, which kills the in-flight wake subprocess (if any) for just that agent and releases the attachment. P1 keeps its other agents either way.

Take-over has a 60-second timeout (kill is 10s). If the holder is wedged on a long LLM wake and doesn't honor the request in time, the requester errors out (or, for `kill`, escalates to a whole-process SIGTERM as a last resort).

## Definitions

Each agent has a definition file: a JSON document describing how to invoke its CLI for each verb. Built-in defaults ship in `apps/a8s/definitions/`:

| File | Purpose |
|---|---|
| `claude.json` | Claude Code with `--permission-mode dontAsk` allowlist + `--continue` |
| `gemini.json` | Gemini CLI with `--yolo` (Policy Engine doesn't apply in headless mode; tracked upstream) + `--resume latest` |
| `codex.json` | Codex CLI with `--full-auto` workspace-write sandbox + `resume --last` |
| `default.json` | Fallback — runs `dummy-cli` and prints "no real CLI configured" |

### The single verb

Every wake reads `definition["invoke"]` — one argv per definition. There is no verb dispatch and no special-case branches: `prompt` and `clear` are gone. Every message is a `tell` with a force-stamped agent `from`, so the same argv shape covers every wake.

Strict opacity (issues #69, #70) still holds: a routed message looks identical whether it arrived directly or via alias fan-out — `$RECIPIENT` resolves to whatever the sender wrote in `to` (the alias name for fanned messages, the agent name for direct ones). Mailing-list semantics.

### Schema

```json
{
  "description": "...",
  "invoke": ["claude", "...", "--continue", "-p", "$SENDER tells $RECIPIENT ($AGE): $MESSAGE"]
}
```

Argv elements run through six substitutions:
- `$SENDER` → sender's canonical name (always non-empty — every message has a force-stamped agent `from`).
- `$RECIPIENT` → what the sender wrote in `to` (alias name for fanned messages, agent name for direct ones).
- `$MESSAGE` → the message body (`content`, with any `FILE: <path>` lines appended).
- `$TIMESTAMP` → ISO 8601 UTC timestamp the message was queued (e.g. `2026-04-28T14:30:00.123456Z`). Useful when you want a stable machine-readable time.
- `$AGE` → human-readable age relative to now (e.g. `5 minutes ago`). Computed at wake time, so a long backlog gets accurate values per message. Pick this OR `$TIMESTAMP` per definition based on which the LLM will read more naturally.
- `$A8S_DIR` → `apps/a8s/` itself, so definitions can point at bundled scripts (`default.json` uses this for `dummy-cli`).

`$TIMESTAMP` and `$AGE` are empty for any message without a `date` field (defensive — every `_write_outbox` stamps one).

Override per-agent with `a8s define <name> <path>` — point at any JSON. The file isn't moved or copied; the registry stores the path.

### Recipient transparency

The default definitions follow the opacity rule — `$SENDER tells $RECIPIENT: $MESSAGE` works equally well whether `$RECIPIENT` is an LLM session, a Python script, or (someday) an SMS gateway. Customize at your own risk.

## State on disk

```
~/.a8s/
├── a8s.json                  registry: { agents: {...}, aliases: {...} }
├── network.json              configured remotes (absent → local-only)
├── seen-ids                  cluster-wide ULID ring for receive-side dedup
├── log.txt                   process-scoped supervisor log
└── agents/
    └── <NAME>/
        ├── inbox/            pending JSON messages (drained by wake_once)
        ├── inbox.tmp/        maildir-style atomic stage for fan-out
        ├── pending/          messages a8s has ingested from .outbox/
        │                     awaiting full delivery — `<ulid>.json` plus
        │                     optional `<ulid>.json.retry` sidecar tracking
        │                     attempts + per-remote success
        ├── trash/             processed messages
        ├── log.txt            per-agent log (wakes, routing, subprocess output)
        └── pid                handler attachment

<agent-root>/
└── .outbox/                  agent writes here; a8s renames out — never
                              read-modify-writes — to ~/.a8s/agents/<NAME>/pending/
```

The outbox lives in the agent's own dir because some sandboxes (codex `--full-auto`) only let the agent write inside its workspace. Inbox/trash/pending live under `~/.a8s/` where the agent can't see them — and per the agent-directory invariant, a8s never sidecars or rewrites in `.outbox/`. New outbox files are atomically renamed to `pending/` on every routing pass; everything from there (sidecars, retries, trash, remote publishes) happens in `~/.a8s/`.

`from` is force-overwritten at routing time. An agent that hand-writes a JSON with `from: "VICTIM"` doesn't get to spoof — the file's outbox location is the unforgeable identity.

## Source layout

```
apps/a8s/
├── a8s.py            entry shim (~30 lines)
├── core.py           paths, logging, Participant, helpers, MARKER_FILES
├── registry.py       ~/.a8s/a8s.json I/O + alias resolution + sender_from_cwd
├── mailbox.py        ensure_mailboxes, route_outboxes (ingest+process), queue helpers
├── definitions.py    invoke* verbs, prompt formatting, definition loading
├── daemon.py         wake subprocess, pid attachment, signal handling
├── ulid.py           pure-stdlib ULID generator/parser (message IDs)
├── network.py        ~/.a8s/network.json + publish_with_backoff + receive loop
├── transports/       Transport ABC + per-kind implementations
│   ├── __init__.py   abstract publish/subscribe/start/stop interface
│   └── mqtt.py       MQTT transport (paho-mqtt impl; persistent session, QoS 1)
├── commands.py       every cmd_*
├── cli.py            COMMANDS table, dispatch, main
├── definitions/      built-in JSONs (claude/gemini/codex/default)
├── dummy-cli         fallback bash script
├── skills/           tell skill (installable into Claude / Gemini / Codex)
└── tests/
    ├── agents/       per-tool fixture dirs (CLAUDE/GEMINI/CODEX/Llama)
    ├── fixtures/     mock-cli + mock.json for end-to-end tests
    ├── requirements.txt   test-only deps (paho-mqtt for transport tests)
    ├── conftest.py   pytest scaffolding (sys.path + fake_home fixture)
    └── test_*.py     ~230 tests, runs in <3s
```

## Testing

```bash
python3 -m pytest apps/a8s/tests/
```

Tests are isolated via a `fake_home` fixture that monkey-patches `HOME` to a tmp dir, so they never touch the real `~/.a8s/`. The daemon tests run real subprocesses against `tests/fixtures/mock-cli` (a deterministic bash script that echoes its argv) so wake_once's argv expansion and routing fan-out can be asserted on the per-agent log.

## Roadmap

Pre-v1 — the surface still moves. Tracked threads:

- **#63 transport extensions** — MQTT (paho-mqtt impl) is the first transport (`a8s remote add`); follow-up PRs add a pure-stdlib mini-MQTT fallback that auto-activates when paho-mqtt isn't installed (same `mqtt` config kind), an HTTPS long-poll transport for self-hosted rendezvous, and a peer-to-peer TCP transport. App-level envelope encryption (per-network PSK, AES-GCM) lands as an implementation detail of specific remote types when wanted.
- **#62** — Cross-cluster file payloads. `FILE:` entries currently stay local-only across remotes; cross-cluster transfer needs a payload host (TempFile.org-style ephemeral storage with signed URLs and per-message symmetric keys) so the sender's bytes can move with the message envelope.
- **#39** — GitHub Copilot CLI as a fourth tool kind. Trivial after the verb-scheme refactor: a `copilot.json` definition + an entry in `core.MARKER_FILES`.

Beyond what's filed: human participants via SMS/email connectors; synchronous `tell --wait <id>` via message-id completion polling on `trash/`; web/local UI; shared knowledge stores between teams.

## Pre-v1 / scorch-the-earth note

a8s has not reached v1. Surface, storage layout, and definition schemas change between phases without migration paths. Existing `~/.a8s/` state may need to be wiped and re-derived through `a8s discover` + `a8s add` after a breaking change. Once the design settles into v1, that contract changes.
