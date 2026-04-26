---
name: "tell"
description: "Send a message to a recipient by name using the `tell` shell command. Optional file attachments via trailing FILE lines."
---

# /tell — send a message to a recipient by name

Use the `tell` shell command (available on PATH) to send a message to a recipient by name.

```
tell <name> <message>
```

- `<name>` is the recipient's name. Treat it as opaque — do not assume whether the recipient is a person or another assistant, and do not change your tone based on a guess.
- `<message>` is the body. To attach files, append one or more `FILE: <absolute-path>` lines at the end. Lines starting with `FILE: ` are stripped from the body and added as attachments.

The command returns immediately; delivery is asynchronous and you do not need to wait for a reply.

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
