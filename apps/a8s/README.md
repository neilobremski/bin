# a8s — Agent Infinity System

Filesystem-based message routing between independent Claude Code, Gemini, and Codex project directories. Plug in any existing project as a participant; a8s does the bookkeeping.

## Design principles

1. **Generic participants.** Participants do not need to know they are part of a8s. Drop in any existing Claude Code / Gemini / Codex project directory unchanged. The only thing a participant must know is how to use the `/tell` skill — and that is installed at the tool's user scope, not into the project itself.

2. **Recipient transparency.** A `/tell` recipient may be another assistant *or* a human reading messages by hand. Senders cannot tell which. Skill descriptions, wake-up prompts, and any user-facing copy inside the system avoid the word "agent" — they say `<name>`, "recipient," or "participant" instead. (Internal a8s code/docs may still call them agents.)

3. **Zero project footprint.** Skills are installed at user scope:
   - Claude Code: `~/.claude/commands/tell.md`
   - Gemini: `~/.gemini/commands/tell.toml`
   - Codex: equivalent user-scope location (TBD)

   a8s never writes into the user's project directories — with one exception below.

4. **Mailboxes are split: outbox local, inbox/trash isolated.** `.inbox/` and `.trash/` live under `~/.a8s/mailboxes/<NAME>/` — the agent never sees them and only interacts with messaging through the `tell` / `says` skills. `.outbox/` lives **inside** the agent's project root because the agent has to write to it, and a strict workspace sandbox (e.g. codex `--full-auto`) can only write within its own workdir. Routing re-stamps the `from` field to the enclosing participant's name on every read, so an agent can't spoof a senderless prompt or impersonate another sender by writing a hand-crafted JSON.

5. **Each participant runs with CWD set to its own root** so its own settings (`.claude/settings*`, `.gemini/`, etc.) load correctly.

## What a8s does

- Scans one or more directories for participant roots — directories containing `CLAUDE.md`, `GEMINI.md`, or `CODEX.md`. Default scan root is the current directory; override with `--dir <path>`.
- Maps each participant's name (and aliases) to its directory.
- Watches each participant's `<root>/.outbox/` for outgoing message JSON; routes them to the recipient's `~/.a8s/mailboxes/<NAME>/.inbox/`. On read, the `from` field is force-set to the actual enclosing participant's name (defense against an agent spoofing the sender by hand-writing JSON).
- When a participant's inbox has messages, launches the participant with a prompt built from the **first** message and immediately moves that message to its `.trash/` (also under `~/.a8s/mailboxes/<NAME>/`).
- Enforces single-instance-per-name (the same name cannot run concurrently with itself).
- After a participant process exits, re-checks its inbox; if more messages remain, prompt again.
- Captures stdout/stderr and prefixes each line with `NAME> `.

### Name parsing

The first `#` heading line of the marker file gives the name, stopping at the first special character.

| First line                                          | Name      |
| --------------------------------------------------- | --------- |
| `# CLAUDE.md`                                       | `CLAUDE`  |
| `# GEMINI.md: Digital Organism Workspace Mandates`  | `GEMINI`  |
| `# code review notebook`                            | `code`    |

If two participants resolve to the same name, they are numbered by directory creation date: `CLAUDE 1`, `CLAUDE 2`, …

### Subprocess invocation

a8s wakes participants by resuming their latest conversation:

Each is invoked with CWD = the participant's directory. The exact flags depend on whether the participant is being resumed (default) or started fresh (after `clear`), and whether `a8s` was launched with `--unrestricted`.

| Type   | Default                                                                                                                              | `--unrestricted`                                                                                                       |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| Claude | `claude --permission-mode dontAsk --allowedTools "<list>" [--continue] -p "<prompt>"`                                                | `claude --dangerously-skip-permissions [--continue] -p "<prompt>"`                                                     |
| Gemini | `gemini --yolo [--resume latest] --prompt "<prompt>"`                                                                                | (same — see below)                                                                                                     |
| Codex  | `codex exec [resume --last] --full-auto --skip-git-repo-check "<prompt>"`                                                            | `codex exec [resume --last] --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check "<prompt>"`               |

The bracketed flags appear unless a fresh start was queued by `a8s clear` (or it's the first wake of a participant with no prior session — claude needs `clear` first; gemini and codex tolerate missing history).

### Permission model per tool

Headless tool use is the whole point of waking a participant — to deliver a message and let it act on it, including using `/tell` to reply. The default mode is the *minimum* permissive setting that lets each tool actually run tools without hanging:

**Claude — granular allowlist.**

Default flags: `--permission-mode dontAsk --allowedTools "<list>"`. `dontAsk` denies everything by default (silently — no prompts that would hang in `-p` mode), and `--allowedTools` pre-approves the tools we ship with. The current default list:

```
Bash(tell:*) Bash(says:*) Read Edit Write Glob Grep WebFetch WebSearch TodoWrite
```

To extend per project, add to `<participant>/.claude/settings.json`:

```json
{ "permissions": { "allow": ["Bash(npm test:*)", "Bash(git diff:*)"] } }
```

These rules *layer on top* of a8s's default allowlist — settings.json applies in headless mode.

**Gemini — `--yolo` only.**

Gemini has a TOML Policy Engine for granular allowlisting (`~/.gemini/policies/*.toml` with `commandPrefix = "tell"` rules), but the engine **does not currently apply in headless `-p` mode** — see [`google-gemini/gemini-cli#20469`](https://github.com/google-gemini/gemini-cli/issues/20469). Until that's fixed upstream, `--yolo` is the only way to enable tools in non-interactive mode. Track and revisit.

**Codex — workspace sandbox.**

`--full-auto` runs codex in a workspace-write sandbox with auto-approval — it can edit files in the project tree but not outside, and won't prompt for any tool. `--unrestricted` drops the sandbox to full-bypass for cases where codex needs to touch files outside its workspace.

### Trust boundary

A participant woken by a8s can run arbitrary commands within whatever permission scope its tool grants. Treat each participant directory as a trust boundary; do not register projects you wouldn't trust to run unattended.

### `--unrestricted`

`a8s --unrestricted [step|loop|...]` drops every available gate:
- Claude → `--dangerously-skip-permissions` (allowlist no longer enforced)
- Codex → `--dangerously-bypass-approvals-and-sandbox` (sandbox dropped)
- Gemini → no change (already maxed out due to upstream limitation)

## Commands

| Command                               | Behavior                                                                                                                                                |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `a8s` (no arguments)                  | **Step mode.** Run one pass of the routing loop, prompt for input, repeat — similar to `psql`. Good for interactive testing without multiple terminals. |
| `a8s loop [names...]`                 | Run continuously until `Ctrl+C` or a sibling `a8s stop`.                                                                                                |
| `a8s prompt <name> "<message>"`       | Queue a **senderless** message in `<name>`'s inbox. The next `step`/`loop` pass wakes `<name>` and delivers the raw prompt (no `from:` wrapper). Safe to run while `a8s loop` is active in another terminal. |
| `a8s prompt all "<message>"`          | Same, but queue the message in **every** discovered participant's inbox. Useful for roll calls and global instructions.                                 |
| `a8s clear`                           | Wipe every participant's mailbox dirs and flag each for a fresh conversation on its next wake.                                                          |
| `a8s install`                         | Install every skill under `apps/a8s/skills/` into each supported tool's user scope. Idempotent.                                                         |
| `a8s logs <name> [--tail N] [-f]`     | Print supervisor-log lines mentioning `<name>` (like `docker logs`). `--tail N` limits output, `-f` follows.                                            |
| `a8s stop`                            | Signal any running `a8s loop` to exit.                                                                                                                  |
| `a8s --dir <path>`                    | Set the scan root for participant discovery.                                                                                                            |
| `a8s --interval <seconds>`            | Loop poll interval (default `1.0`).                                                                                                                     |
| `a8s --unrestricted`                  | Wake participants in their full-permissions mode (claude `--dangerously-skip-permissions`, gemini `--yolo`, codex `--dangerously-bypass-...`).          |

Without `loop`, a8s makes one pass and waits for any launched processes to exit before exiting itself.

## Message format

Messages are JSON files dropped into the sender's outbox at `<sender-root>/.outbox/`:

```json
{
    "date": "2024-01-01T12:00:00Z",
    "from": "NAME",
    "to": "NAME",
    "content": "MESSAGE_CONTENT",
    "files": [
        {"filename": "example.txt", "path": "/path/to/example.txt"}
    ]
}
```

When delivered, the participant is woken with a recipient-neutral prompt. The shape depends on `from` and `to`:

```
[{date}] {from} tells you ({to}): {content}    # direct       (from set, to set)
[{date}] {from} says: {content}                # broadcast    (from set, to empty)
{content}                                      # raw prompt   (from empty)

FILE: {files[0].path}
```

`FILE:` lines are omitted when there are no files. The direct template includes the recipient's own name in parens so the wake context is unambiguous about who is being addressed. Recipients of a broadcast are not told who else got it (the recipient list is implicit and ephemeral). A senderless message (queued by `a8s prompt`) is delivered as raw content — no wrapper. The template never identifies the sender as human or AI.

## The `tell` and `says` CLIs and the registry

a8s ships two sender-side shell commands, both siblings of `a8s` in `~/bin/`. Both write a message JSON into the **caller's** local `<root>/.outbox/` and exit — routing happens later when `a8s` next runs (step or loop) and re-stamps the message with the actual sender name.

| Command | What it does                                                      | Outbox `to` field |
| ------- | ----------------------------------------------------------------- | ----------------- |
| `tell <name> <message>` | Direct message to a specific named recipient.       | the recipient name |
| `says <message>`        | Broadcast to every other registered participant.    | empty (`""`)       |

The room metaphor: `tell` is whispering to one person; `says` is speaking up so the room hears. The CLI is named `says` (third-person) rather than `say` to avoid clashing with macOS's `/usr/bin/say` (text-to-speech).

To know who the *caller* is, `tell` walks up from `$PWD` looking for a directory whose absolute path matches an entry in the **registry** at `~/.a8s/a8s.json`. The registry is populated automatically every time `a8s` runs:

```json
{
  "CLAUDE": {"kind": "claude", "root": "/abs/path", "aliases": []},
  "WORK":   {"kind": "claude", "root": "/abs/path", "aliases": ["w"]}
}
```

- New participants are added when `a8s` discovers them.
- Name conflicts (same name → different root) are warned and skipped; resolution is by manual edit.
- Aliases are not yet auto-populated; edit them in by hand.

The recipient is resolved by name first, then by alias.

If `$PWD` is not inside any registered participant, `tell` fails with a clear error — it does not silently default to a generic sender.

### Skill installation

`a8s install` walks `apps/a8s/skills/*/SKILL.md` and installs each skill into every supported tool. Idempotent.

| Tool   | What `a8s install` does                                                                                                                                                                            |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Claude | Symlinks `bin/docs/<name>.md` → `apps/a8s/skills/<name>/SKILL.md`. Your existing `bin/install.sh` then symlinks it into `~/.claude/skills/<name>/SKILL.md` on the next shell open.                |
| Gemini | Calls `gemini skills link <skill-dir> --scope user --consent` so updates to the canonical skill propagate live. Skip if `gemini` isn't on PATH; skip if the skill is already linked.              |
| Codex  | Symlinks the skill directory to `~/.codex/skills/<name>` (codex's user-scope skill location). Skip if `~/.codex/skills` doesn't exist.                                                            |

## Example layout

```
projects/
  my-claude-project/
    CLAUDE.md
    .outbox/                       ← agent writes here (sandbox-writable)
  my-gemini-project/
    GEMINI.md
    .outbox/

~/.a8s/                            ← supervisor-only state
  a8s.json                         ← participant registry
  log.txt                          ← supervisor log (ISO-timestamped lines)
  mailboxes/
    CLAUDE/.inbox/.trash/          ← keyed by registered name (sanitized);
    GEMINI/.inbox/.trash/            agents never see these
```

```
$ cd projects
$ a8s loop
$ a8s prompt my-claude-project "Ask my-gemini-project to summarize ./notes.md"
$ a8s stop
```

### Supervisor log

Every line a8s prints (system messages, routing decisions, prefixed agent output) is also appended to `~/.a8s/log.txt` with an ISO-8601 UTC timestamp. The same `PRINT_LOCK` that keeps stdout interleaving clean across concurrent loop workers also guards the log file, so log lines stay atomic.

Use `a8s logs <name>` to filter the log for a specific participant — same shape as `docker logs`:

```bash
a8s logs CLAUDE              # all log lines mentioning CLAUDE
a8s logs CLAUDE --tail 100   # last 100
a8s logs CLAUDE -f           # tail-follow new lines as they're written
```

Matching is case-insensitive on word boundaries, so a lookup for `CLAUDE` catches `CLAUDE> ...`, `[CLAUDE] waking ...`, and `routed: GEMINI -> CLAUDE` lines alike.

The log grows without rotation in v1 — truncate or rotate manually if needed.

### Reset / fresh start

`a8s clear` removes every queued, in-flight, and processed message from all participants' mailboxes, and records a one-shot "fresh" flag (in `~/.cache/a8s/fresh.json`) so that each participant's *next* wake omits the resume flag — i.e. starts a brand-new conversation rather than resuming the previous one. The flag is consumed on first wake; subsequent wakes resume normally.

This is the recommended way to reset state when a participant gets wedged in a stuck conversation.

### Skill authoring note

YAML frontmatter scalars in `SKILL.md` files **must be quoted** if their values contain `:` or other YAML-significant characters. Codex's parser is strict and will silently fail to load a skill with unquoted scalars; Gemini's parser is more tolerant. Use double-quoted strings as a safe default:

```yaml
---
name: "tell"
description: "Send a message ... mentioning FILE: paths and other tricky chars."
---
```

## Local-model participants (Claude Code → Ollama)

Local-model agents live in `tests/experimental-agents/` rather than the default `tests/agents/` mesh, because the skill abstraction doesn't reach the model through ollama and tool-use is unreliable enough that they're a noise source in protocol tests. Run `a8s --dir tests/experimental-agents` to engage with them.

Claude Code can be pointed at a local Ollama endpoint by setting `ANTHROPIC_BASE_URL=http://localhost:11434` (Ollama's native `/v1/messages` endpoint speaks Anthropic protocol, including `tool_use` blocks). See `tests/experimental-agents/llama-agent/.claude/settings.json` for the working config.

**What works.** Direct shell-command tool use (`Run: tell GEMINI hi`) works fine through Claude Code → Ollama, including with relatively small models (qwen3.5:latest reliably produces correctly-shaped Bash tool calls).

**What doesn't.** Claude Code's *skill abstraction* (loading `~/.claude/skills/<name>/SKILL.md` and asking the model to "use the tell skill") is unreliable with local models — even capable ones often produce a final answer like "DONE" without ever emitting the tool call. The skill abstraction relies on a system-prompt translation step that local models flub.

**Workaround.** Bypass the skill abstraction in the agent's own `CLAUDE.md` by instructing it about the `tell` and `says` shell commands directly:

```markdown
If you're asked to message someone privately by name, run: `tell <NAME> "<MESSAGE>"`
If you're asked to address everyone, run: `says "<MESSAGE>"`
```

`tests/experimental-agents/llama-agent/CLAUDE.md` does this. Routing then works end-to-end.

**Model recommendation.** `llama3.2:3b` is too small for reliable tool use even with direct instructions. Use `qwen3.5:latest` or comparable as the default for local-model participants. Adjust `model` in the participant's `.claude/settings.json`.

**Auth-conflict warning.** Claude Code will print `Auth conflict: Both a token (ANTHROPIC_AUTH_TOKEN) and an API key (/login managed key) are set` if you've ever run `claude /login` previously. Functionality is unaffected, but to silence the warning, run `claude /logout` once at the user level.

## Recovery model (v1)

Messages are transient. The inbox file is moved to `.trash/` *before* the participant has produced a response. If the participant crashes or gets wedged mid-prompt, the message is gone — re-prompting is just as risky as loss (it can pile state on top of a broken conversation). v1 documents the gap and leaves recovery to the human operator. A future version will need a real recovery story (see below).

## Future

- **Recovery.** Some Claude conversations get stuck in a state where the only fix is starting fresh. Need a prompting structure that lets a participant either resume from prior context or knowingly start over.
- **Per-participant run flags** declared in the participant's directory, e.g. `--dangerously-skip-permissions` / `--yolo`.
- **Docker isolation** so a participant can be sandboxed with a curated set of mounts and binaries.
- **Human participants** as first-class peers (the recipient-transparency rule already anticipates this).
- **Aliases** for participants (the README mentions them but the format is undecided).
- **Terminology.** "Agent" may not be the right word once humans are routinely on the other end. Candidates: participant, peer, correspondent, node.
