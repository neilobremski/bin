---
name: "tell"
description: "Send a message to a recipient by name using the `tell` shell command. Delivery is asynchronous and may take seconds, minutes, or longer for the recipient to receive and act on the message; do not wait for or expect an immediate reply. Optional file attachments via trailing FILE lines."
---

# /tell — send a message to a recipient by name

Use the `tell` shell command (available on PATH) to send a message to a recipient by name.

```
tell <name> <message>
```

- `<name>` is the recipient's name. Treat it as opaque — do not assume whether the recipient is a person or another assistant, and do not change your tone based on a guess.
- `<message>` is the body. To attach files, append one or more `FILE: <absolute-path>` lines at the end. Lines starting with `FILE: ` are stripped from the body and added as attachments.

The command returns immediately. Delivery and processing are asynchronous: the recipient may receive the message seconds, minutes, hours, or longer after you send it, and may take additional time to read and act on it. Don't make assumptions about what causes the delay — it isn't necessarily mechanical. Do not block waiting for a reply; if you need a response, send the message and continue with other work. If a reply is essential, ask the user how to proceed rather than polling.

## Examples

```
tell GEMINI please summarize the attached note
FILE: /tmp/note.txt
```

```
tell w heads up — running the migration tonight
```

## Failures

- **"current directory is not inside any registered participant"** — `tell` could not determine who the message is from. Report this to the user and stop; do not try to work around it.
- **"no participant named or aliased X"** — recipient is unknown. Report it; do not invent or guess names.
