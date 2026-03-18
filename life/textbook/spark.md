# Life Spark — Layer 0

The spark is the heartbeat. Deliberately dumb.

## Properties

- No intelligence. No LLM, no API calls, no network access.
- No persistence. Runs, checks, launches, exits.
- Idempotent. Running it twice produces the same result.
- Fast. Under 1 second.

## Configuration

The spark sources `life.conf` — a shell file that sets `ORGANS` and any environment the organism needs.

```bash
# life.conf
ORGANS=organs/brain:organs/memory
MQTT_HOST=broker.example.com
BRIDGE_KEY=secret
```

All variables become environment for organ processes.

**Discovery order**: argument > working directory > home directory.

## Algorithm

```
1. Find and source life.conf
2. Split ORGANS on : into organ directories
3. For each organ directory:
   a. VALIDATE: does live.sh exist and is it executable?
      no -> skip
   b. CADENCE: if organ.conf has cadence and .spark.last exists,
      check elapsed minutes. Too soon -> skip
   c. SINGLETON: flock on ~/.life/locks/<name>.lock
      locked -> skip (already running)
   d. LAUNCH: run live.sh, capture output to .spark.log,
      write epoch to .spark.last
```

## Cadence

Set in `organ.conf` (sourceable shell, same as `life.conf`):

```bash
CADENCE=5
```

Minutes between launches. No `organ.conf` = runs on stimulus only (dormant). No `CADENCE` = runs every cycle.

## Singleton

The spark enforces one-instance-per-organ via `flock`. Lock files live at `~/.life/locks/<organ-name>.lock`. The lock is held for the duration of `live.sh` and released automatically when the process exits. No stale PID problem.

## Failure Modes

| Failure | Result |
|---------|--------|
| No life.conf found | Clean exit (code 0) |
| No ORGANS set | Clean exit |
| live.sh missing | Skip organ, log warning |
| organ.conf missing | Dormant (runs on stimulus only) |
| live.sh fails | Spark retries next cycle |
| Spark crashes | Cron restarts next minute |
