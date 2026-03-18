# Immune System

The immune system keeps the organism healthy without a brain. Every body part runs a **lymph node** organ that scans local organs and cleans up problems.

## The Lymph Node

The lymph node is a periodic organ. Each cycle:

1. Walk all organ directories in `ORGANS`
2. Check `health.txt`: missing, stale (mtime too old), or starts with `error`/`degraded`
3. Check `stimulus.txt`: too many lines → truncate to last N
4. Check `.spark.log`: too large → truncate to last N bytes
5. Write its own `health.txt` summarizing findings
6. Publish summary to MQTT (retained) for cross-body-part visibility

If everything is healthy: `ok N organs checked`
If something is wrong: `degraded 2 issues: heart:stale ganglion:dropped`

## Thresholds

Configurable via environment (set in `life.conf` or `organ.conf`):

| Variable | Default | Meaning |
|----------|---------|---------|
| `STALE_SECONDS` | 300 | health.txt older than this = stale |
| `MAX_STIMULUS_LINES` | 100 | Truncate stimulus.txt beyond this |
| `MAX_LOG_BYTES` | 1048576 | Truncate .spark.log beyond 1MB |

## What It Does NOT Do

- No alerting. No brain, no mouth — it just reports.
- No killing organs. The spark handles lifecycle.
- No network calls (except optional MQTT publish of summary).

## Distributed Health

Each body part runs its own lymph node. Each publishes a retained summary to MQTT:

```
head/health → "ok 5 organs checked"
phone/health → "degraded sms:error"
```

A central consumer (future brain) subscribes to `+/health` and gets the full organism picture. The lymph nodes don't know about each other — they just emit.

## Ganglion Integration

The ganglion reports dropped messages as `degraded` in its own `health.txt`. The lymph node catches this through its normal health scan — no special coupling needed.
