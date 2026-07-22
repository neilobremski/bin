# h4l — Hall

Multi-agent chat rooms as a standalone CLI. Register it as an a8s participant
when you want agents to coordinate in shared rooms without stuffing full
transcripts into every wake.

## Install

`~/bin/h4l` is on PATH after `source ~/bin/install.sh`. Implementation lives in
`apps/h4l/`.

## Standalone use

```bash
mkdir -p ~/chat-node
h4l dispatch --root ~/chat-node --from ALICE --node HALL --message '#war hello'
h4l dispatch --root ~/chat-node --from BOB --node HALL --message '/view war'
h4l clear --root ~/chat-node --older-than 3600
```

State: `~/chat-node/.chatrooms/rooms/<slug>/`.

Notifications call `tell` on PATH. For local testing without outbox setup:

```bash
h4l dispatch --root ~/chat-node --from ALICE --node HALL \
  --simulate-tell --message '/post war hello'
# or: H4L_SIMULATE_TELL=1 h4l dispatch ...
```

Simulated tells print to stderr as `h4l> tell <agent>:` lines. Use `--no-notify`
to silence tell entirely (pytest).

## a8s wiring

```bash
a8s add chatroom ~/chat-node apps/a8s/connectors/h4l/example-definition.json
a8s start chatroom
tell chatroom '#war hello'
tell chatroom '/list'
```

The registered agent name (`chatroom`, `hall`, etc.) becomes `--node` / `$RECIPIENT`
and appears in notification footers.

## IRC-style usage

Post to a channel the IRC way — no slash command needed:

```bash
tell chatroom '#everyone hello'
```

Same as `tell chatroom '/post everyone hello'`. Room names accept an optional `#`
prefix everywhere (`#war`, `war`, `/join #war`).

| IRC | h4l |
|-----|-----|
| `#chan message` | `#chan message` or `/post chan message` |
| `/join #chan` | `/join chan` |
| `/part #chan` | `/part chan` or `/leave chan` |
| `/names #chan` | `/names chan` or `/members chan` |
| `/list` | `/list` |
| (scrollback) | `/view chan` with pagination footer |

**Differences from IRC:** async `tell` delivery (not a live socket); posting
auto-creates the channel and joins you; `/invite` for adding agents; `/view` for
history with cursor hints; open/trusted membership (no channel modes or bans).

## Commands

| Command | Example |
|---------|---------|
| *(post)* | `#war hello` |
| `/post` | `/post war hello` (alias) |
| `/join` | `/join war` |
| `/leave` | `/leave war` (`/part` alias) |
| `/invite` | `/invite war BOB CAROL` |
| `/remove` | `/remove war BOB` (`/kick` alias) |
| `/list` | `/list` |
| `/view` | `/view war [start limit] [--start N] [--limit N]` |
| `/members` | `/members war` (`/names` alias) |
| `/help` | `/help` |

`/view` returns a convo-style markdown transcript (latest 10 messages by default).
A footer reports the viewed message range and total count, with `tell` commands to
page older/newer (`--start`), latest (bare `/view`), or an arbitrary window
(`/view war <start> <limit>`).

Room slugs: `[a-z0-9_-]+`, case-insensitive, stored lowercase.

- Posting auto-creates the room and auto-joins the poster.
- Malformed input → `tell` error back to sender.
- Successful post → stdout ACK + `tell` ACK to sender; other members get truncated notify.
- Attachments: a8s wake appends `ATTACHED FILE:` lines; hall re-`tell --attach`s them to
  other room members (one inbound attach, N outbound copies via a8s).

## Tests

```bash
python3 -m pytest apps/h4l/tests/
```
