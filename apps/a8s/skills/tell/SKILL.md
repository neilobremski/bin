---
name: "tell"
description: "Send a message to a recipient by name using the `tell` shell command. Delivery is asynchronous and may take seconds, minutes, or longer for the recipient to receive and act on the message; do not wait for or expect an immediate reply. Attach files with `--attach` or `--file`."
---

# tell — send a message by name

Use the `tell` shell command (on PATH) to send a message to a recipient by name.

```
tell [--attach PATH] <name> [<message...>]
```

**Invoke `tell` through your shell tool.** Printing `tell BOB "hello"` as assistant text does not send anything — only a real shell invocation delivers the message.

## Recipients and tone

- `<name>` is opaque. Do not assume the recipient is a person, another assistant, or a group — and do not change tone based on a guess.
- `<message>` is the body. Omit it when piping stdin (see below).

## Options

- **`--attach PATH` / `--file PATH`** — attach a file (repeatable). Flags may appear before or after the recipient name, before the message body.
- **Stdin** — pass `-` to read stdin explicitly, or pipe with no message argument:

```
echo "summarize this" | tell GEMINI
cat report.md | tell GEMINI -
```

**Legacy `FILE:` lines** (still supported): trailing `FILE: <path>` lines at the end of the body, or `FILE: ./path` as a separate shell argument.

## Timing

The command returns immediately. Delivery is asynchronous — the recipient may act seconds, minutes, or longer later. To wait for a reply, use the `tells` command (see the `tells` skill); if a reply is essential and you cannot wait, ask the user how to proceed.

## Examples

```
tell GEMINI --attach /tmp/note.txt please summarize the attached note
tell w heads up — running the migration tonight
git diff | tell CLAUDE review these changes
```

## Failures

- **`tell: cannot send from this directory`** — you are not in a context where sending works. Report this to the user; do not `cd` elsewhere to work around it.
- **Unknown recipient names** are not rejected at the CLI. Routing decides what happens; use names you actually know.
