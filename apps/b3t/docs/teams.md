# Microsoft Teams (`b3t teams` / `b3t tm`)

Reads PTSA leadership traffic out of Teams so it can be curated like any
other submission source.

## Commands

```bash
b3t teams login                       # Verify auth
b3t teams list                        # List chats and channels
b3t teams sweep                       # Every conversation, recent messages
b3t teams sweep --chat "Board Retreat" --since 14
b3t teams sweep --json                # Machine-readable
b3t teams save --dir editions/YYYY-MM-DD/submissions
```

## How it works

Teams web has no usable read API for personal chats, so this reads the
rendered message list.

| Piece | Selector |
|-------|----------|
| Rail entries (chats + channels) | `[role=treeitem]`, first line of `innerText` |
| Message container | `[data-tid=chat-pane-item]` |
| Author | `[data-tid=message-author-name]` |
| Timestamp | `time[datetime]` (ISO, **UTC**) |
| Body | `[data-tid=chat-pane-message]` |

Rows without an author element are date separators and system notices, and
are skipped.

## Gotchas

- **Timestamps are UTC.** The `datetime` attribute is UTC while the visible
  label is local, so a message shown as `8/26 5:44 PM` carries
  `2026-08-27T00:44Z`. `_local_stamp()` converts for output. Do not print the
  raw attribute.
- **The list is virtualized.** Scrolling up unloads the newest rows. The sweep
  pins to the bottom first, then reads at every scroll step and de-duplicates,
  rather than scrolling and reading once.
- **Team names are not conversations.** `RMS PTSA Leadership` is a team
  header; its channel is `General` underneath.
- **Channels do not parse yet.** Chats work. A channel click leaves the
  message pane empty with no `chat-pane-item` and no `time` elements, so
  `sweep` returns nothing for `General` and `PTSA Communications`. Chats carry
  the traffic today; channels are unfinished work, not a silent failure.
- Auth is the same Microsoft 365 session as `b3t outlook`.
