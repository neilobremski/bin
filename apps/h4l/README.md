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
h4l dispatch --root ~/chat-node --from ALICE --node HALL --message '/post war hello'
h4l dispatch --root ~/chat-node --from BOB --node HALL --message '/view war'
h4l clear --root ~/chat-node --older-than 3600
```

State: `~/chat-node/.chatrooms/rooms/<slug>/`.

Notifications call `tell` on PATH (skip with `--no-notify` for tests).

## a8s wiring

```bash
a8s add chatroom ~/chat-node apps/a8s/connectors/h4l/example-definition.json
a8s start chatroom
tell chatroom '/post war hello'
tell chatroom '/list'
```

The registered agent name (`chatroom`, `hall`, etc.) becomes `--node` / `$RECIPIENT`
and appears in notification footers.

## Slash commands

| Command | Example |
|---------|---------|
| `/post` | `/post war hello world` |
| `/join` | `/join war` |
| `/leave` | `/leave war` |
| `/invite` | `/invite war BOB CAROL` |
| `/list` | `/list` |
| `/view` | `/view war` |
| `/members` | `/members war` |

Room slugs: `[a-z0-9_-]+`, case-insensitive, stored lowercase.

- `/post` auto-creates the room and auto-joins the poster.
- Malformed input → `tell` error back to sender.
- Successful `/post` → stdout ACK + `tell` ACK to sender; other members get truncated notify.

## Tests

```bash
python3 -m pytest apps/h4l/tests/
```
