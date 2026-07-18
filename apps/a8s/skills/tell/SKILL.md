---
name: "tell"
description: "Send an asynchronous message with the `tell` CLI. Delivery is not immediate; do not wait for a reply."
---

# tell

Send messages via the shell (not by printing the command as text):

```
tell [--attach PATH] <recipient> [<message...>]
```

- `<recipient>` is an opaque name — do not guess who/what it is or change tone.
- Omit `<message>` when piping stdin (`echo hi | tell BOB`, or `tell BOB -`).
- `--attach` / `--file` may repeat; flags may appear before or after `<recipient>`.
- Returns immediately. Delivery may take seconds or longer; do not expect a reply in-session.
- If `tell` fails with “cannot send from this directory”, tell the user — do not `cd` to work around it.
