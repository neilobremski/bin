# tell — internals

Operator documentation for how `tell` works under the hood. Agent-facing usage lives in [`skills/tell/SKILL.md`](../skills/tell/SKILL.md).

## Surface

| Entry | Path |
|-------|------|
| Operator shim | `~/bin/tell` → `~/bin/a8s tell` |
| System client | `sudo a8s install-client` → `/usr/local/bin/tell` + `/usr/local/lib/a8s/` |
| Implementation | `apps/a8s/tell.py` (`tell_main`) |
| Router | `apps/a8s/mailbox.py` (`route_outboxes`) |
| Sync protocol | `apps/a8s/sync_listen.py` |

## Send path (async)

1. Walk up from CWD for the first `.outbox/` directory.
2. If none found, walk up from `TELL_DEFAULT_DIR` (agent root or any path under it).
3. Build message body (argv, stdin, or `-`); parse trailing `FILE:` lines via `mailbox._split_content_and_files`.
4. Optionally read `~/.a8s` (or `A8S_HOME`) to validate recipient and stamp `from` when CWD sits inside a registered agent root.
5. Write a JSON envelope atomically into `.outbox/` (`.{id}.tmp` → `{id}.json`).

Envelope shape:

```json
{
  "id": "<ulid>",
  "date": "<iso8601 Z>",
  "to": "<recipient>",
  "content": "...",
  "files": [{"filename": "...", "path": "..."}],
  "from": "<sender>"
}
```

`from` is omitted when registry is unreachable; the router **force-overwrites** `from` based on which agent owns the outbox directory.

6. `route_outboxes` ingests outbox files into `~/.a8s/agents/<NAME>/pending/`, routes to recipient inboxes (or alias fan-out), and handles remotes.

### `TELL_DEFAULT_DIR`

Set on an agent process (systemd, wrapper script, `.env`) to an agent root or any directory beneath it. When CWD is outside the agent tree — e.g. `/tmp` — `tell` still finds `.outbox/` via this fallback. CWD wins when it already encloses an outbox.

```bash
export TELL_DEFAULT_DIR=/home/knobert/my-agent
cd /tmp && tell GEMINI "works from anywhere"
```

Does not affect `sender_from_cwd()`; the router still force-stamps `from` from outbox ownership.

## `--sync`

Client side (`tell.py` + `sync_listen.py`):

1. Write the user message envelope as usual.
2. Write a control envelope to `!a8s` with `command: sync_listen` and session paths under `<agent-root>/.temp/`.
3. Poll for `listen.ack`, then `{session}.reply.json`, then drop `sync_cancel` on exit.

Server side (`sync_listen.py` via a8s handler):

- Registers listeners in `~/.a8s/agents/<NAME>/sync-listeners.json`.
- When a matching reply is routed, writes the reply JSON to the agent-root path `tell` polls.

Designed for containers and file-proxy mounts where only the agent tree is shared.

## install-client

`a8s install-client [dest]` copies this `apps/a8s` tree (minus `tests/`) to `/usr/local/lib/a8s/` by default and installs `/usr/local/bin/tell`:

```bash
exec python3 "/usr/local/lib/a8s/a8s.py" tell "$@"
```

Re-run to upgrade; replaces existing wrapper (including symlinks). Requires root for `/usr/local`.

Use case: agent OS users who must not read the operator's `~/bin` tree — they get `tell` only on PATH.

## User-visible opacity

CLI help and the agent skill intentionally avoid mentioning `.outbox/`, `.temp/`, or JSON. Errors use generic messages (`cannot send from this directory`) so curious agents are not steered toward bypassing the tool.

## Related

- [a8s README](../README.md) — mental model diagram, routing invariants
- `apps/a8s/tests/test_tell.py` — CLI behavior
- `apps/a8s/tests/test_sync_listen.py` — sync round-trip
