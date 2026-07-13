# tell — internals

Operator documentation for how `tell` works under the hood. Agent-facing usage lives in [`skills/tell/SKILL.md`](../skills/tell/SKILL.md).

## Surface

| Entry | Path |
|-------|------|
| Operator shim | `~/bin/tell` → `~/bin/a8s tell` |
| System client | `sudo a8s install-client` → `/usr/local/bin/tell` + `/usr/local/lib/a8s/` |
| Implementation | `apps/a8s/tell.py` (`tell_main`) |
| Router | `apps/a8s/mailbox.py` (`route_outboxes`) |
| Receive-side | `apps/a8s/tells.py` (`tells_main`) — see below |

## Send path (async)

0. **`tell --check`** — optional self-test: verifies `TELL_OUTBOX_DIR` points at a writable outbox (creates the path when missing). Optional recipient name validates registry routing. No envelope written.
1. **`TELL_OUTBOX_DIR` required** — tell writes only to this path. a8s sets it on every wake/idle/batch invoke from the agent definition's `outbox_dir` (default `<agent-root>/.outbox`). Manual use: export it explicitly.
2. Build message body (argv, stdin, or `-`); parse trailing `FILE:` lines via `mailbox._split_content_and_files`. `--attach` / `--file` append to the same `files` array. Any source path tell can read is attachable. Allocate `msg_id`, copy each file into `<outbox>/<msg_id>/<basename>`, then write `<outbox>/<msg_id>.json` with **filename-only** `files` entries (no `path` field).
3. Optionally read `~/.a8s` (or `A8S_HOME`) to validate recipient and stamp `from` when CWD sits inside a registered agent root.

Envelope shape:

```json
{
  "id": "01J…",
  "date": "<iso8601 Z>",
  "to": "<recipient>",
  "content": "...",
  "files": [{"filename": "avatar.jpg"}],
  "from": "<sender>"
}
```

On disk alongside the JSON:

```
<outbox>/
  01J….json
  01J…/
    avatar.jpg
```

`from` is omitted when registry is unreachable; the router **force-overwrites** `from` based on which agent owns the outbox directory.

4. **Ingest** — move `<msg_id>.json` and `<outbox>/<msg_id>/` together into `~/.a8s/agents/<SENDER>/pending/`.
5. **Route** — copy pending bundle bytes into each recipient's `<files_dir>/<msg_id>/` (default `.files`). Inbox JSON keeps filename-only `files`. Wake `$MESSAGE` appends absolute `ATTACHED FILE:` lines.

### `TELL_OUTBOX_DIR`

The outbox path tell writes to. **Required** — no CWD walk or implicit discovery.

```bash
export TELL_OUTBOX_DIR=/var/mailboxes/agent-one/.outbox
tell GEMINI "hello"
```

Created when missing.

When a8s wakes an agent, it sets `TELL_OUTBOX_DIR` in the invoke subprocess environment to the agent definition's resolved `outbox_dir` (default `<agent-root>/.outbox`). Use a separate absolute `outbox_dir` to keep outgoing tell traffic outside the agent workspace.

Does not affect `sender_from_cwd()`; the router still force-stamps `from` from outbox ownership.

## `tells` (receive side)

`tells [--timeout SEC]` (`apps/a8s/tells.py`) is the receive-side complement of `tell`. It resolves the node from `TELL_OUTBOX_DIR` exactly as `tell` does — the file-proxy inbox is `.inbox` beside the outbox (`<outbox-parent>/.inbox`).

1. Snapshot the `.json` envelopes already in `.inbox`.
2. Poll (0.1s) up to `--timeout` seconds (default 5) for new envelopes.
3. Print each new envelope as `sender: body` to stdout and exit 0; on timeout print one stderr line and exit 1.

Non-destructive: it observes new arrivals without consuming them, so it never competes to remove `.inbox` files and each run waits from its own baseline. Partial writes (mid-delivery on a cross-mount move) are tolerated — an unreadable file is skipped and retried on the next poll. It only reports messages that land after it starts; anything already waiting is ignored.

## install-client

`a8s install-client [dest]` copies this `apps/a8s` tree (minus `tests/`) to `/usr/local/lib/a8s/` by default and installs `/usr/local/bin/tell`:

```bash
exec python3 "/usr/local/lib/a8s/a8s.py" tell "$@"
```

Re-run to upgrade; replaces existing wrapper (including symlinks). Requires root for `/usr/local`.

Use case: agent OS users who must not read the operator's `~/bin` tree — they get `tell` only on PATH.

## User-visible opacity

CLI help and the agent skills intentionally avoid mentioning `.outbox/`, `.inbox/`, or JSON. Errors use generic messages (`cannot send from this directory`, `cannot receive from this directory`) so curious agents are not steered toward bypassing the tool.

## Related

- [a8s README](../README.md) — mental model diagram, routing invariants
- `apps/a8s/tests/test_tell.py` — CLI behavior
- `apps/a8s/tests/test_tells.py` — receive-side wait behavior
