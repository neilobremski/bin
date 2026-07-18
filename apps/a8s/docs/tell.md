# tell — internals

Operator documentation for how `tell` works under the hood. Agent-facing usage
lives in [`skills/tell/SKILL.md`](../skills/tell/SKILL.md) (send-only). Desktop /
filedrop setup: [filedrop.md](filedrop.md).

## Surface

| Entry | Path |
|-------|------|
| Operator shim | `~/bin/tell` → `~/bin/a8s tell` |
| System client | `sudo a8s install-client` → `/usr/local/bin/tell` + `/usr/local/lib/a8s/` |
| Implementation | `apps/a8s/tell.py` (`tell_main`) |
| Router | `apps/a8s/mailbox.py` (`route_outboxes`) |
| Receive-side | `apps/a8s/tells.py` (`tells_main`) — see below |

## Send path (async)

0. **`tell --check`** — optional self-test: verifies the resolved outbox is writable (creates the path when missing). Optional recipient name validates registry routing. No envelope written.
1. **`TELL_OUTBOX_DIR` or CWD filedrop** — tell writes to the env path when set;
   otherwise may resolve a unique configured outbox from CWD when the registry
   is reachable (see [filedrop.md](filedrop.md)). `install-client`
   tell-only installs always need the env var.
2. Build message body (argv, stdin, or `-`); parse trailing `FILE:` lines via `mailbox._split_content_and_files`. `--attach` / `--file` append to the same `files` array (`--attach=PATH` and multiple paths after one flag are supported). Oversized sources fail immediately unless `--split` chunks them under `TELL_FILE_MAX` / `max_file_bytes`. Allocate `msg_id`, copy each file into `<outbox>/<msg_id>/<basename>`, then write `<outbox>/<msg_id>.json` with **filename-only** `files` entries (no `path` field).
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

## `TELL_OUTBOX_DIR`

The outbox path tell writes to.

**Priority:**

1. `TELL_OUTBOX_DIR` when set (required for deployed agents — a8s injects it on wake).
2. Else a unique configured outbox matched from CWD when `~/.a8s` is readable
   (desktop / filedrop seats — see [filedrop.md](filedrop.md)).
3. Else fail.

```bash
export TELL_OUTBOX_DIR=/var/filedrops/agent-one/.outbox
tell GEMINI "hello"
```

Created when missing.

When a8s wakes an agent, it sets `TELL_OUTBOX_DIR` in the invoke subprocess environment to the agent definition's resolved `outbox_dir` (default `<agent-root>/.outbox`). Use a separate absolute `outbox_dir` to keep outgoing tell traffic outside the agent workspace.

Does not affect `sender_from_cwd()`; the router still force-stamps `from` from outbox ownership.

## `tells` (receive side)

`tells [-f] [--timeout SEC] [--glow [THEME]] [--heading-out|in …]` (`apps/a8s/tells.py`)
is the receive-side complement of `tell`. It resolves the node the same way
`tell` does — the file-proxy inbox is `.inbox` beside the outbox
(`<outbox-parent>/.inbox`).

1. Snapshot the `.json` envelopes already in `.inbox`.
2. Poll (0.1s) up to `--timeout` seconds (default 5) for new envelopes; `-f` /
   `--timeout 0` follows until Ctrl+C.
3. Print each new envelope as `sender: body` by default. With `--glow` and/or
   `--heading-out` / `--heading-in`, print the same markdown as `a8s convo`
   (shared `format_entry` / GlowStream). Timeout prints one stderr line and exits 1.

Non-destructive: it observes new arrivals without consuming them, so it never competes to remove `.inbox` files and each run waits from its own baseline. Partial writes (mid-delivery on a cross-mount move) are tolerated — an unreadable file is skipped and retried on the next poll. It only reports messages that land after it starts; anything already waiting is ignored.

## install-client

`a8s install-client [dest]` copies this `apps/a8s` tree (minus `tests/`) to `/usr/local/lib/a8s/` by default and installs `/usr/local/bin/tell`:

```bash
exec python3 "/usr/local/lib/a8s/a8s.py" tell "$@"
```

That install has no `~/.a8s` access by design — always set `TELL_OUTBOX_DIR`.

## Tests

- `apps/a8s/tests/test_tell.py` — send path, attachments, outbox resolution
- `apps/a8s/tests/test_tells.py` — receive-side wait behavior
