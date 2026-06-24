# a8s — Development Notes

Historical decisions, hard constraints, and things that didn't work.
Read `README.md` first for concept and usage.

## Hard constraints when refactoring

- **`cmd_start` re-execs via `core.ENTRYPOINT`**, not `__file__`.
- **Argv interpolation** (`$SENDER`, `$RECIPIENT`, `$MESSAGE`, `$TIMESTAMP`,
  `$AGE`, `$A8S_DIR`) expands via `definitions._expand_argv`. Per-message
  wakes use `invoke` via `build_command`; batch wakes use `batch.invoke` via
  `build_batch_command` with message file paths appended as trailing argv.
- **`core.PRINT_LOCK` is the cross-module log lock.** Only set when
  `daemon.attached_loop` starts.
- **`run_with_prefix` uses `start_new_session=True`** — don't drop this.
- **Per-agent take-over via detach-request (no orphans).** Don't reintroduce
  process-level SIGTERM-and-wait in `acquire`.
- **Per-agent kill via kill-request + SIGUSR1.** Handler checks at iteration top.
- **Agent-directory invariant — `.outbox/` is one-way.** a8s never reads or
  writes sidecars there. Ingest is atomic rename into `pending/`.
- **Remote routing publishes to all configured remotes.** Receivers dedupe by ULID.
- **Cross-cluster `FILE:` payloads ride storage services.** Configured under
  `network.json`'s `services` map (separate from `remotes`).
- **Storage services are stateless.** No start/stop lifecycle.
- **Absolute attachment paths in wake prompts.** Delivered messages append `ATTACHED FILE: <absolute-path>` lines (not bare `FILE:`). Path comes from definition `files_dir` (default `.files` under agent root) plus `<msg_id>/<filename>`.
- **Outbox attachments are staged.** Tell copies sources into `.outbox/<msg_id>/`; outbox envelopes carry `filename` only. Ingest moves the bundle with the JSON. Routing delivers into `<files_dir>/<msg_id>/`. Delivered wakes append `ATTACHED FILE:` lines (not bare `FILE:`).
- **Definition `outbox_dir`.** Optional; defaults to `.outbox` under agent root. Absolute paths allowed. Harness ingests from the resolved path; wakes inject `TELL_OUTBOX_DIR` into the invoke subprocess so tell writes there without the agent seeing the outbox in its workspace.
- **Tell requires `TELL_OUTBOX_DIR`.** No CWD tree walk — a8s sets the env on wake; manual tell must export it explicitly.
- **Persistent MQTT sessions.** `clean_session=False` + QoS 1, hash-derived `client_id`.
- **`publish` waits for readiness event before raising.** Don't drop the
  disconnect handler.
- **Per-message backoff retry.** BACKOFF_SCHEDULE drives `.retry` sidecars.
- **Local routing claims the ULID in `seen-ids`** to prevent MQTT round-trip dupes.
- **`settings.json` is the stable operator config.** `a8s config set` persists
  machine-wide keys; `a8s config` (no args) catalogs every knob including
  definition, registry, and network fields. Env vars apply only when a key
  is absent from the file. `A8S_HOME` relocates the whole state dir.
- **`conversations.jsonl` is machine-wide.** One routed row per logical message
  (alias fan-out is one row). Rotates at `convo_max_limit` (default 1000).
  Queried by `a8s convo <agent>` — not per-agent storage.

## Per-tool quirks

- **Claude Code** — `--permission-mode dontAsk` + `--allowedTools "..."`. `--continue` for continuity.
- **Gemini CLI** — `--yolo` REQUIRED in headless mode. Policy Engine doesn't apply to `-p`.
- **Codex CLI** — `--full-auto`. `stdin=subprocess.DEVNULL` REQUIRED (hangs otherwise).
- **Copilot CLI** — `--allow-all-tools` REQUIRED. Marker is `.github/copilot-instructions.md`.
- **OpenCode** — `opencode run "<msg>"`. `--dangerously-skip-permissions` required. Model in agent's `opencode.json`.

## What didn't work

- Synchronous `a8s prompt` — raced with the loop. Queue into inbox instead.
- Mailboxes inside agent dirs — Gemini surfaced them to the model. Moved to `~/.a8s/agents/`.
- Headless tool-use without auto-approval — hangs silently. Always pass the flag.
- Singleton daemon — replaced with per-agent handlers.
- `says` broadcast verb — LLMs couldn't pick tell vs says consistently.
- Senderless `prompt`/`clear` commands — security hole over MQTT. Removed.
- `--unrestricted` global flag — retired. Use custom definition files instead.

## Active design threads

| # | State | Topic |
|---|---|---|
| #63 | partial | Multi-cluster routing. MQTT in, mini-MQTT/HTTPS/TCP/encryption still open. |
| #72 | open | Mailbox file format discussion. |
| #93 | open | Grok CLI as tool kind. |
