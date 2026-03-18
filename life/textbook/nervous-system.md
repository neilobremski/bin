# Nervous System

The nervous system lets organs discover and signal each other without knowing where anything lives. An organ says "send this to a heart" and the nervous system figures out the rest.

## Organs Have Type and ID

Every organ has a **type** (what it does) and an **ID** (which one it is). The type comes from the directory name. The ID is assigned by the body part — typically `type-bodypart` (e.g., `heart-aws`, `heart-hp`).

An organ doesn't know or care about IDs. It thinks in types: "signal a tail," "how are the hearts doing?" The nervous system resolves types to specific organs.

## The Ganglion

Each body part runs one ganglion. The ganglion is the nervous system's local node. It knows:

- What organs exist locally (from the `ORGANS` env var)
- Each organ's type and health (by reading their `health.txt`)
- What organs exist remotely (by talking to other ganglions)

The ganglion maintains a **registry** — a SQLite database of every organ it knows about, local and remote. Each cycle, it:

1. Scans local organs and records their current health
2. Broadcasts its local organ list to other ganglions
3. Receives broadcasts from other ganglions, updates the registry
4. Delivers any incoming stimuli to local organs (writes to `stimulus.txt`)

The registry is **eventually consistent**. Health status reflects the last ganglion cycle, not real-time. This is by design — biological systems don't poll organs at 60fps. The delay gives organs time to recover before being flagged, and lets the ganglion track health over time.

## The Stimulus CLI

Organs interact with the nervous system through the `stimulus` command. They never touch MQTT or write to other organs' files directly.

```bash
# Signal an organ by type (any one of that type will receive it)
stimulus send tail "swim now"

# Query health of all organs of a type
stimulus query heart
# heart  aws       ok beat 42       2m ago
# heart  hp        ok beat 17       1m ago

# Include a circulatory reference
stimulus send tail "food circ:a1b2c3d4"
```

### Two Operations

**Send by type** — "deliver this to any organ of type X." Delivered to the first local match. If no local match, published to MQTT for remote delivery.

**Query by type** — "give me the health of all organs of type X." Reads from the local registry. No network round-trip — the registry is pre-populated by ganglion-to-ganglion broadcasts.

## Health Is Local

An organ updates its own `health.txt`. That's it. No MQTT, no network call. The ganglion reads it locally each cycle and shares it with other ganglions. This means:

- An organ's health is always writable, even if the network is down
- The ganglion is the only thing that reads `health.txt` for external consumption
- The immune system (lymph node) can query the ganglion's registry instead of scanning directories

## Transport

The ganglions currently talk to each other over MQTT. But the design doesn't depend on MQTT — any pub/sub or message queue would work. The `stimulus` CLI and `health.txt` files are the stable interfaces. The wire between ganglions is an implementation detail.

MQTT details (current implementation):
- Ganglions use persistent sessions so no messages are lost between cycles
- Stimulus messages use QoS 1 (guaranteed delivery)
- Health broadcasts are retained (latest wins)
- Each ganglion has a stable client ID for session persistence

## No Network, No Problem

If `MQTT_HOST` is unset, the ganglion still works — it just can't see remote organs. Local stimulus delivery and health tracking work fine. A body part in isolation is degraded, not dead.
