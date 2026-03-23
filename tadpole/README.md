# Tadpole

A test organism with two body parts: a brain in Docker and a body on the host, connected by MQTT.

```
                    MQTT (localhost:1883)
                         |
        +----------------+----------------+
        |                                 |
  [Brain - Docker]                  [Body - Host]
   PFC (claude -p)                  comms (email I/O)
   hippocampus                      ganglion (router)
   ganglion (router)                heart, tail, stomach
                                    lymph, hippocampus
        |                                 |
        +----------- circ (bind) ---------+
                  ~/.life/circ
```

The brain thinks (PFC runs `claude -p` to generate replies). The body handles everything else: receiving emails, routing signals, heartbeat, memory, digestion, and immune checks. They share a circulatory directory (`~/.life/circ`) for passing payloads, and communicate signals over MQTT.

## Prerequisites

- **Docker** (for the brain container)
- **mosquitto** (`apt install mosquitto mosquitto-clients`) — local MQTT broker
- **Bedrock env vars** for the brain's claude CLI: `ANTHROPIC_BEDROCK_BASE_URL`, `ANTHROPIC_CUSTOM_HEADERS`, etc.

## Quick Start

```bash
# Run with mock Gmail (default — no credentials needed)
./tadpole.sh

# Run with real Gmail (requires GAS bridge credentials)
./tadpole.sh --real
```

One command starts everything: mosquitto broker (if not running), body organs via spark-loop, and brain in Docker. Ctrl-C stops it all.

## Interacting (Mock Gmail)

In a second terminal, drop a JSON file into the mock inbox:

```bash
# Send tadpole an email
cat > ~/.tadpole/gmail/inbox/001.json <<'EOF'
{
  "id": "001",
  "from": "you@example.com",
  "subject": "Hello",
  "body": "Hi tadpole! What are you up to?",
  "labels": ["UNREAD", "Tadpole"]
}
EOF

# Check for replies
cat ~/.tadpole/gmail/sent/*.json
```

## Configuration

Environment variables (all optional):

| Variable | Default | Purpose |
|----------|---------|---------|
| `SPARK_INTERVAL` | 60 | Seconds between spark cycles |
| `CIRC_DIR` | `~/.life/circ` | Shared circulatory directory |
| `GMAIL_MOCK_DIR` | `~/.tadpole/gmail` | Mock email directory |
| `MQTT_HOST` | localhost | MQTT broker host |
| `MQTT_PORT` | 1883 | MQTT broker port |
| `GANGLION_LISTEN_DURATION` | 30 | Seconds ganglion listens per cycle |

## Organs

| Organ | Body Part | Cadence | Role |
|-------|-----------|---------|------|
| PFC | brain (Docker) | stimulus | Thinks, generates email replies |
| hippocampus | both | 1 min | Stores and consolidates memories |
| ganglion | both | 1 min | Routes signals, maintains registry |
| heart | body (host) | 1 min | Heartbeat, writes health |
| comms | body (host) | 1 min | Email I/O (send/receive) |
| tail | body (host) | stimulus | Swims when told to |
| stomach | body (host) | stimulus | Digests food into payloads |
| lymph | body (host) | 1 min | Health checks, overflow cleanup |

## Automated Tests

```bash
cd tadpole
./lifetime.sh
```

Tests chapters 1-4 (heartbeat, signals, digestion, immune response) without Docker or credentials.
