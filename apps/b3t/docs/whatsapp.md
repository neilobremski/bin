# WhatsApp Web (`b3t whatsapp` / `b3t wa`)

Reads the PTSA board group chat so leadership traffic can be curated like any
other submission source.

## Commands

```bash
b3t whatsapp login                    # Report link status
b3t whatsapp list                     # List chats
b3t whatsapp sweep                    # Every chat, recent messages
b3t whatsapp sweep --chat "Board" --since 14
b3t whatsapp sweep --json
b3t whatsapp save --dir editions/YYYY-MM-DD/submissions
```

## Linking (a human step)

WhatsApp Web links to a phone by QR scan. No command can do it.

1. `b3t open`
2. Go to `web.whatsapp.com`, leave **Stay logged in on this browser** ticked
3. Scan the code from the phone
4. `b3t close`

The link persists in the Chrome profile. `b3t whatsapp login` reports the
state so a stale link fails loudly instead of returning an empty sweep.

## How it works

There is no API. Every message bubble carries `data-pre-plain-text` of the
form `[6:59 AM, 9/16/2026] +1 (206) 291-2692: `, which supplies the timestamp
and the sender's number.

| Piece | Selector |
|-------|----------|
| Sidebar chats | `#pane-side [role=row]` → `span[title]` |
| Message bubble | `[data-pre-plain-text]` |
| Body | `[data-testid=selectable-text]` |
| Display name | row `[aria-label^="Maybe "]`, else first line of the row |

## Gotchas

- **`data-pre-plain-text` only carries the phone number.** Group members who
  are not in the phone's contacts show as a number. The display name comes
  from the rendered row, which is why both are captured: `author` and `phone`.
- **The list is virtualized**, same as Teams. The sweep pins to the newest
  message first, then reads at every scroll step and de-duplicates. Scrolling
  up and reading once returns only old messages.
- **Synthetic `.click()` does not open a chat.** Use a real Playwright click.
- Messages arrive out of order across scroll batches, so output is sorted by
  timestamp before printing or saving.
