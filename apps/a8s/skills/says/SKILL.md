---
name: "says"
description: "Broadcast a message to every other participant (room-style speaking). Use the `says` shell command. Append FILE lines to attach files."
---

# /says — broadcast a message to every other participant

Use the `says` shell command (available on PATH) to address everyone present, like speaking up in a room. Use `tell` instead when you mean to message one specific recipient privately.

```
says <message>
```

- `<message>` is the body. To attach files, append one or more `FILE: <absolute-path>` lines at the end. Lines starting with `FILE: ` are stripped from the body and added as attachments.
- The recipient list is implicit: every currently-registered participant other than yourself receives a copy. You do not name recipients with `says`.
- Each recipient sees the message wrapped in a "says" header (vs `tell`'s "tells you (<your-name>)"), making the broadcast nature explicit. Recipients are not told who else got the message.

The command returns immediately; delivery is asynchronous.

## Examples

```
says I just merged the migration; expect a brief blip in the next 5 minutes.
```

```
says new build artifacts ready
FILE: /tmp/build.log
```

## Replying

A recipient may reply privately with `tell <sender> <message>` or back to the group with `says <message>`. Either is fine — choose what fits the context. Don't broadcast back if the reply only matters to the original sender.

## Failures

- **"current directory is not inside any registered participant"** — `says` could not determine who the message is from. Report and stop; do not work around it.
