---
name: "tells"
description: "Wait for the next inbound message to this node using the `tells` shell command. Blocks up to a timeout (default 5s), prints each arriving message (sender and body) to stdout, and exits 0; exits 1 if nothing arrives. The receive-side complement of `tell`."
---

# tells — wait for the next inbound message

Use the `tells` shell command (on PATH) to block until the next message arrives for this node.

```
tells [--timeout SEC]
```

**Invoke `tells` through your shell tool.** It resolves the node the same way `tell` does, so it works wherever `tell` works.

## Behavior

- Waits up to `--timeout` seconds (default 5) for a new message to arrive.
- Prints each arriving message as `sender: body` to stdout and exits 0.
- If several arrive together, prints them all.
- Exits 1 with one line on stderr if nothing arrives in time.
- Only reports messages that arrive after it starts — messages already waiting are left alone.

## Examples

```
tells
tells --timeout 30
```

## Failures

- **`tells: cannot receive from this directory`** — you are not in a context where receiving works. Report this to the user; do not `cd` elsewhere to work around it.
