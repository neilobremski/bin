# Layers: The 3-Layer Activation Model

Lower layers are dumber and more reliable. Higher layers are smarter and more autonomous.

## Layer 0 — Life Spark

**What**: A bash script (`life/spark.sh`).
**Trigger**: Cron every minute.
**Config**: Sources `life.conf` for `ORGANS` and environment.
**Intelligence**: Zero. No LLM, no network calls. Pure filesystem.

The spark does exactly this:
1. Source `life.conf` (exports all vars as environment)
2. Split `ORGANS` on `:` into organ directories
3. For each organ: does `live.sh` exist? Skip if no.
4. If `organ.json` has cadence, check `.spark.last`. Skip if not due.
5. Flock `~/.life/locks/<name>.lock`. Skip if locked (already running).
6. Launch `live.sh`, capture output to `.spark.log`, write `.spark.last`.

The spark is the heartbeat. It provides **periodic activation** — organs that need to run every N minutes get sparked on schedule.

See [spark.md](spark.md) for the full specification.

## Layer 1 — Spinal Cord

**What**: An organ. Sparked like any other organ.
**Purpose**: Bridges real-time signals (MQTT) into per-organ `stimulus.txt`.

1. Cron sparks the spinal cord on its cadence
2. Spinal cord connects to MQTT (`$MQTT_HOST` from `life.conf`)
3. Drains queued messages
4. Appends lines to each target organ's `stimulus.txt`
5. Runs `life/spark.sh` directly to trigger event-driven organ launch
6. Exits

**Layer 0 + Layer 1 together provide both periodic and event-driven activation.**

- Periodic: Cron sparks Layer 0, Layer 0 sparks organs on cadence
- Event-driven: MQTT message arrives, spinal cord writes stimulus, sparks the target organ

The spinal cord is NOT persistent. It runs, drains, writes, sparks, exits.

## Layer 2+ — Organs

Autonomous components with full decision-making within their domain.

Each organ:
- Is sparked by Layer 0
- Reads its own `stimulus.txt` for incoming signals
- Reports health via `health.txt`
- Has full autonomy within its domain
- Singleton enforced by the spark (flock), not by the organ

Organs do not communicate directly. Inter-organ signals flow through MQTT and get routed by the spinal cord into per-organ `stimulus.txt`.

## Why Three Layers

| Property | Layer 0 (Spark) | Layer 1 (Spinal Cord) | Layer 2+ (Organs) |
|----------|----------------|----------------------|-------------------|
| Intelligence | None | Minimal (routing) | Full |
| Persistence | None (cron) | None (sparked) | None (sparked) |
| Network | None | MQTT only | Whatever they need |
| Failure mode | Cron restarts | Spark restarts | Spark restarts |

If Layer 2 dies, Layer 0 restarts it. If Layer 1 dies, Layer 0 restarts it. If Layer 0 dies, cron restarts it. The dumber layers protect the smarter ones.
