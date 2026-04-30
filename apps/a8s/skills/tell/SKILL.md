---
name: "tell"
description: "Send a message to a recipient by name using the `tell` shell command. Delivery is asynchronous and may take seconds, minutes, or longer for the recipient to receive and act on the message; do not wait for or expect an immediate reply. Optional file attachments via trailing FILE lines."
---

# /tell — send a message to a recipient by name

Use the `tell` shell command (available on PATH) to send a message to a recipient by name.

```
tell <name> <message>
```

**`tell` is a shell command — invoke it via your bash/shell tool.** Printing `tell <name> "..."` as your final assistant text is *not* a reply; it is just narration and the message will not be sent. The recipient hears you only when you actually execute `tell` through the shell tool.

**Run from inside an agent's directory tree.** `tell` walks up from CWD to find the first `.outbox/` directory and drops the message JSON there. If no `.outbox/` exists in CWD or any parent, the command errors with `tell: no .outbox/ found in CWD or any parent`.

- `<name>` is the recipient's name. Treat it as opaque — do not assume whether the recipient is a person or another assistant, and do not change your tone based on a guess.
- `<message>` is the body. To attach files, append one or more `FILE: <path>` lines at the end. Lines starting with `FILE: ` are stripped from the body and added as attachments. Paths can be absolute or relative to your current directory.

**Quoting**: the body may be one quoted argument (with embedded newlines for FILE: lines) OR multiple shell arguments — `tell` joins them. A `FILE:`-prefixed argument is automatically lifted onto its own line, so both shapes work:

```
tell alice "Here's the doc.
FILE: ./report.pdf"
```

```
tell alice "Here's the doc." "FILE: ./report.pdf"
```

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

- **"tell: no .outbox/ found in CWD or any parent"** — you ran `tell` outside any agent's directory tree. Report this to the user and stop; do not try to work around it (e.g. don't `cd` somewhere unusual to make the error go away).
- **The recipient name is not validated.** `tell` accepts any `<name>` and the routing layer decides what to do with it. If the name is unknown to the router and no remote clusters are configured, the message is logged + trashed silently. Use names you actually know.
