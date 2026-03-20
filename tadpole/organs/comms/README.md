# Comms Organ — Stimulus-Driven Email I/O

The comms organ handles email communication for the tadpole. It does NOT
check email on its own — it only acts when told to via stimulus signals.

## Cadence

`CADENCE=1` — runs every minute to process pending stimulus.

## Message Contract (Brain <-> Comms)

### Brain -> Comms

| Signal | Description |
|--------|-------------|
| `check-email [query]` | Search Gmail, notify brain of each unread email |
| `send-reply <thread_id> circ:<hash>` | Send reply body (from circ) to the thread |

### Comms -> Brain

| Signal | Description |
|--------|-------------|
| `new-email <thread_id> circ:<hash>` | New email found, full content stored in circ |
| `sent <thread_id>` | Reply successfully sent |

## Gmail Muscle

The `gmail` CLI abstracts Gmail access with automatic fallback:

- **Primary**: GAS bridge (`gas gmail.*` commands)
- **Fallback**: If GAS returns rate-limit errors, automatically switches to
  Gmail REST API using a cached OAuth token from `gas token.get`

Subcommands: `search`, `get`, `reply`, `label`

## Async Flow

```
Brain (every 15 min)
  |-- no pending emails --> stimulus send comms "check-email"
  |
Comms (every 1 min)
  |-- consumes "check-email"
  |-- gmail search "label:Tadpole is:unread"
  |-- for each email: gmail get, circ-put, stimulus send brain "new-email ..."
  |
Brain (next cycle)
  |-- consumes "new-email <id> circ:<hash>"
  |-- circ-get email content
  |-- memories search for context
  |-- claude -p --model haiku generates reply
  |-- circ-put reply body
  |-- stimulus send comms "send-reply <id> circ:<reply_hash>"
  |
Comms (next cycle)
  |-- consumes "send-reply <id> circ:<hash>"
  |-- circ-get reply body
  |-- gmail reply <id> --body-file <circ-cache-path>
  |-- gmail label <id> --remove UNREAD
  |-- memories store interaction
  |-- stimulus send brain "sent <id>"
```
