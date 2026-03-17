# Layers: The 3-Layer Activation Model

The organism activates through three layers. Lower layers are dumber and more
reliable. Higher layers are smarter and more autonomous.

## Layer 0 -- Life Spark

**What**: A bash script (`life/spark.sh`) that runs on every machine.
**Trigger**: Cron every minute (`* * * * *`). For sub-minute frequency, use
systemd timers.
**Config**: Discovers organ directories via argument, `$ORGANS` env, local `organs.conf`, or `~/organs.conf` (first match wins).
**Intelligence**: Zero. No LLM, no network calls. Pure filesystem checks.

The spark does exactly this:
1. Discover organ directories (see [spark.md](spark.md) for 4 discovery methods)
2. For each organ: does `live.sh` exist and is it executable? -- skip if no
3. Is it already running? (`kill -0` PID check) -- skip if yes
4. If `organ.json` exists, check cadence -- skip if not yet due
5. All clear: launch `live.sh` via nohup, record PID and timestamp

The spark is the heartbeat. It provides **periodic activation** -- organs that
need to run every N minutes get kicked by the spark on schedule.

See [spark.md](spark.md) for the full specification.

## Layer 1 -- Spinal Cord

**What**: An organ (not a daemon). Kicked by the spark like any other organ.
**Purpose**: Bridges real-time signals (MQTT) into organ-local stimulus files.
**How it works**:
1. Spark kicks the spinal cord on its cadence
2. Spinal cord connects to MQTT broker (TLS 8883)
3. Drains queued messages from subscribed topics
4. Routes each message to the target organ's `stimulus.jsonl`
5. After writing stimulus, calls `life/spark.sh` directly (event-driven trigger)
6. Disconnects and exits

This is the key insight: **Layer 0 + Layer 1 together provide both periodic
and event-driven activation.**

- Periodic: Cron kicks spark, spark kicks organs on cadence
- Event-driven: MQTT message arrives, spinal cord writes stimulus, spinal cord
  kicks spark, spark sees the organ needs attention and launches it

The spinal cord is NOT persistent. It runs, drains, writes, kicks, exits.
The spark will kick it again next cycle to drain any new messages.

## Layer 2+ -- Organs

**What**: Autonomous components with full decision-making within their domain.
**Examples**: Brain, circadian rhythm, future research organ, future memory organ.
**Contract**: `live.sh` (required) + optional `organ.json` and `health.json` (see [organ-contract.md](organ-contract.md))

Each organ:
- Is launched by the spark (Layer 0)
- Reads its own `stimulus.jsonl` for incoming signals (written by Layer 1)
- Reports health via `health.json`
- Has full autonomy to decide what to do within its domain
- Runs as a singleton (one instance at a time, enforced by flock)

Organs do not communicate directly with each other. All inter-organ
communication flows through the nervous system (MQTT) and gets routed
by the spinal cord (Layer 1) into per-organ stimulus files.

## Why Three Layers

| Property | Layer 0 (Spark) | Layer 1 (Spinal Cord) | Layer 2+ (Organs) |
|----------|----------------|----------------------|-------------------|
| Intelligence | None | Minimal (routing) | Full (LLM, heuristics) |
| Persistence | None (cron) | None (spark-kicked) | None (spark-kicked) |
| Network | None | MQTT only | Whatever they need |
| Failure mode | Cron restarts | Spark restarts | Spark restarts |
| Singleton | N/A | Yes (flock) | Yes (flock) |

If Layer 2 dies, Layer 0 restarts it. If Layer 1 dies, Layer 0 restarts it.
If Layer 0 dies, cron restarts it. The dumber layers protect the smarter ones.
