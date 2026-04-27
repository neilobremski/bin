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

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ~/.a8s/                                      │
│                                                                     │
│   a8s.json     ┌─────── agents/CLAUDE/ ─────┐  ┌── log.txt ──┐      │
│   (registry)   │ inbox/ trash/ log.txt pid │  │  process    │      │
│                └────────────────────────────┘  │  events     │      │
│   { agents,    ┌─────── agents/GEMINI/ ─────┐  └─────────────┘      │
│     aliases }  │ inbox/ trash/ log.txt pid │                        │
│                └────────────────────────────┘                       │
└─────────────────────────────────────────────────────────────────────┘

┌──────────── agent root: ~/projects/foo/ ─────────────┐
│   CLAUDE.md  (or GEMINI.md, CODEX.md — marker file)  │
│   .outbox/   (agent writes JSON; routed by handler)  │
└──────────────────────────────────────────────────────┘
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
| `a8s kill <name>` | Etiquette-then-force: SIGTERM, brief grace, SIGTERM again (kills the wake subprocess group), SIGKILL fallback. |
| `a8s exit` | SIGTERM every running handler. |
| `a8s ls` | List only running agents and their handler PIDs. |

### Messaging
| | |
|---|---|
| `a8s tell <name> <msg>` | Routed message. `<name>` may be an agent or alias (fans out at routing time). Sender = agent enclosing CWD. |
| `a8s prompt <name> <msg>` | Senderless supervisor message — delivered raw, no template wrapping. |
| `a8s clear <name>` | Queue a CLEAR sentinel. Inbox is wiped at write time and at read time; the next wake runs `invokeClear`. Aliases iterate. |
| `a8s logs <name>... [--tail N] [-f]` | Read per-agent log files; merge-sort by ISO timestamp across multiple agents. `-f` follows. |

### Skills
| | |
|---|---|
| `a8s install` | Install canonical skills (`tell`) into Claude / Gemini / Codex user scope. |

`a8s` with no command prints help. There is no auto-discovery of agents from CWD — registration is always explicit.

### Take-over collateral

`start`/`run`/`step` always win. If another handler holds the agent, the new caller SIGTERMs it, waits for it to detach, then atomically claims the pid file. If the prior handler was multi-agent (handling an alias), it detaches **all** its agents — the new caller takes only what it asked for. Other agents in the prior set become orphaned. Documented footgun; restart them explicitly.

## Definitions

Each agent has a definition file: a JSON document describing how to invoke its CLI for each verb, plus prompt templates. Built-in defaults ship in `apps/a8s/definitions/`:

| File | Purpose |
|---|---|
| `claude.json` | Claude Code with `--permission-mode dontAsk` allowlist + `--continue` |
| `gemini.json` | Gemini CLI with `--yolo` (Policy Engine doesn't apply in headless mode; tracked upstream) + `--resume latest` |
| `codex.json` | Codex CLI with `--full-auto` workspace-write sandbox + `resume --last` |
| `default.json` | Fallback — runs `dummy-cli` and prints "no real CLI configured" |

### The four verbs

The wake routine reads the message and selects one of:

| Verb | Trigger | Body |
|---|---|---|
| `invokePrompt` | `from` is empty (queued by `a8s prompt`) | Raw `content`, delivered as-is |
| `invokeMessage` | `from` is set, no alias context | `promptMessage` template formatted |
| `invokeMessageAlias` | `from` is set, message arrived via alias | `promptMessageAlias` template (sees alias name + others-count, NOT the other recipients' names — opacity rule) |
| `invokeClear` | `clear: true` sentinel | No prompt; runs the CLI fresh to start a new conversation |

### Schema

```json
{
  "description": "...",
  "invokePrompt":       ["claude", "...", "--continue", "-p", "$PROMPT"],
  "invokeMessage":      ["claude", "...", "--continue", "-p", "$PROMPT"],
  "invokeMessageAlias": ["claude", "...", "--continue", "-p", "$PROMPT"],
  "invokeClear":        ["claude", "-p", "Conversation cleared. New conversation starts now."],
  "promptMessage":      "{sender} tells you ({recipient}): {message}",
  "promptMessageAlias": "{sender} tells you ({recipient}) and {others_count} others on the {alias} alias: {message}"
}
```

Argv elements run through two substitutions:
- `$PROMPT` → the wake's prompt body (formatted via the matching template, or raw for `invokePrompt`).
- `$A8S_DIR` → `apps/a8s/` itself, so definitions can point at bundled scripts (`default.json` uses this for `dummy-cli`).

Override per-agent with `a8s define <name> <path>` — point at any JSON. The file isn't moved or copied; the registry stores the path.

### Recipient transparency

Templates SHOULD NOT leak whether the recipient is a Claude session, a script, or a human via SMS. The four built-in defaults follow this — `{sender} tells you ({recipient})` works equally well for any backend. Customize at your own risk.

## State on disk

```
~/.a8s/
├── a8s.json                  registry: { agents: {...}, aliases: {...} }
├── log.txt                   process-scoped supervisor log
└── agents/
    └── <NAME>/
        ├── inbox/            pending JSON messages (drained by wake_once)
        ├── trash/            processed messages
        ├── log.txt           per-agent log (wakes, routing involving this agent, subprocess output)
        └── pid               handler attachment

<agent-root>/
└── .outbox/                  agent writes here; route_outboxes re-stamps `from` to enclose
```

The outbox lives in the agent's own dir because some sandboxes (codex `--full-auto`) only let the agent write inside its workspace. Inbox/trash live in `~/.a8s/` so the agent can't see them — keeps the abstraction clean.

`from` is force-overwritten at routing time. An agent that hand-writes a JSON with `from: "VICTIM"` doesn't get to spoof — the file's outbox location is the unforgeable identity.

## Source layout

```
apps/a8s/
├── a8s.py            entry shim (~30 lines)
├── core.py           paths, logging, Participant, helpers, MARKER_FILES
├── registry.py       ~/.a8s/a8s.json I/O + alias resolution + sender_from_cwd
├── mailbox.py        ensure_mailboxes, route_outboxes, queue helpers
├── definitions.py    invoke* verbs, prompt formatting, definition loading
├── daemon.py         wake subprocess, pid attachment, signal handling
├── commands.py       every cmd_*
├── cli.py            COMMANDS table, dispatch, main
├── definitions/      built-in JSONs (claude/gemini/codex/default)
├── dummy-cli         fallback bash script
├── skills/           tell skill (installable into Claude / Gemini / Codex)
└── tests/
    ├── agents/       per-tool fixture dirs (CLAUDE/GEMINI/CODEX/Llama)
    ├── fixtures/     mock-cli + mock.json for end-to-end tests
    ├── conftest.py   pytest scaffolding (sys.path + fake_home fixture)
    └── test_*.py     98 tests, runs in <100ms
```

## Testing

```bash
python3 -m pytest apps/a8s/tests/
```

Tests are isolated via a `fake_home` fixture that monkey-patches `HOME` to a tmp dir, so they never touch the real `~/.a8s/`. The daemon tests run real subprocesses against `tests/fixtures/mock-cli` (a deterministic bash script that echoes its argv) so wake_once's argv expansion and routing fan-out can be asserted on the per-agent log.

## Roadmap

Pre-v1 — the surface still moves. Tracked threads:

- **#62** — File transfer. `FILE: <path>` entries currently carry the sender's path verbatim. Routing should stage payloads into `<recipient>/.files/<filename>` so messages with attachments work across sandboxes (and, eventually, machines).
- **#63** — Transparent multi-cluster routing. Two a8s clusters on the same network see each other and route messages as peers. Recipient opacity carries through. Proposed initial transports: MQTT for the control plane, an ephemeral payload host (TempFile.org-style) for files. Spec is implementation-agnostic.
- **#39** — GitHub Copilot CLI as a fourth tool kind. Now trivial after the verb-scheme refactor: a `copilot.json` definition + an entry in `core.MARKER_FILES`.

Beyond what's filed: human participants via SMS/email gateways; synchronous `tell --wait <id>` via message-id completion polling on `trash/`; web/local UI; shared knowledge stores between teams.

## Pre-v1 / scorch-the-earth note

a8s has not reached v1. Surface, storage layout, and definition schemas change between phases without migration paths. Existing `~/.a8s/` state may need to be wiped and re-derived through `a8s discover` + `a8s add` after a breaking change. Once the design settles into v1, that contract changes.
