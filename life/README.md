# Life Spark

A cron-based heartbeat that launches "organs" — autonomous processes that do work on their own schedule. Pure bash, no dependencies beyond coreutils.

The spark is deliberately dumb: read config, find organs, check cadence, enforce singleton, launch. All intelligence lives in the organs themselves.

## Quick Start

Create a `life.conf` in your organism directory:

```bash
ORGANS=organs/brain:organs/memory
```

Run the spark:

```bash
cd my-organism
/path/to/life/spark.sh
```

Install to cron for a continuous heartbeat:

```
* * * * * cd /path/to/my-organism && /path/to/life/spark-cron.sh
```

`spark-cron.sh` wraps `spark.sh` with daily log rotation (`~/.life/spark-YYYY-MM-DD.log`, 7-day retention).

## life.conf

A sourceable shell file. The spark sources it before launching organs, so all variables become environment for every organ process.

```bash
# life.conf — organism configuration
ORGANS=organs/brain:organs/memory:organs/research

# Optional: these become environment variables available to all organs
MQTT_HOST=broker.example.com
MQTT_USER=myuser
BRIDGE_KEY=fe68bcea-xxxx-xxxx-xxxx
```

`ORGANS` is colon-separated paths (relative to the conf file's directory, or absolute).

**Discovery order:** argument > working directory > home directory.

## Organ Contract

An organ is a directory with an executable `live.sh`. That's the only requirement.

| File | Required | Purpose |
|------|----------|---------|
| `live.sh` | Yes | Entry point (must be executable) |
| `organ.json` | No | Cadence configuration |
| `health.txt` | No | Status reporting (first word: `ok`, `degraded`, or `error`) |

The spark manages:

| File | Purpose |
|------|---------|
| `.spark.last` | Epoch seconds of last launch (cadence check) |
| `.spark.log` | stdout/stderr from the organ |

Singleton is enforced by the spark via `flock` — not by the organ. Lock files live at `~/.life/locks/<organ-name>.lock`.

## Cadence

`organ.json` optionally sets how often an organ runs:

```json
{
  "cadence": 5
}
```

Minutes between launches. If omitted (or no `organ.json`), the organ runs every spark cycle.

## Health Reporting

Organs can optionally write `health.txt` to report status. The spark does not read this — it's for monitoring and immune system use.

```
ok processed 42 items
```

The first word is the status (`ok`, `degraded`, or `error`). Everything after is a human-readable message. The file's modification time serves as the timestamp.
