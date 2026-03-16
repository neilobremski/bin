# Life Spark

A cron-based heartbeat that launches "organs" — autonomous processes that do work on their own schedule. Pure bash, no dependencies beyond coreutils. Works on Linux, macOS, and WSL.

The spark is deliberately dumb: discover organs, check if they're due, launch them. All intelligence lives in the organs themselves.

## Install to Cron

```
* * * * * /path/to/life/spark.sh
```

That's it. Every minute, the spark checks which organs need launching.

## Configuring Organs

The spark discovers organ directories in priority order:

1. **Argument** — pass a manifest file path: `spark.sh /path/to/manifest.conf`
2. **Environment** — set `ORGANS` as colon-separated paths (like `PATH`): `ORGANS=/opt/brain:/opt/eyes spark.sh`
3. **Default manifest** — `organs.conf` next to the script (one path per line, `#` comments, blank lines ignored)

If no organs are found, the spark logs a message and exits cleanly.

## Organ Contract

An organ is a directory containing:

| File | Required | Purpose |
|------|----------|---------|
| `live.sh` | Yes | Entry point (must be executable) |
| `organ.json` | No | Configuration (currently: cadence) |

The spark manages these files inside each organ directory:

| File | Purpose |
|------|---------|
| `.spark.pid` | PID of the running organ (singleton check) |
| `.spark.last` | ISO timestamp of last launch (cadence check) |
| `.spark.log` | stdout/stderr from the organ |

## Example organ.json

```json
{
  "cadence": 5
}
```

The `cadence` field is an integer number of minutes between launches. If omitted (or no `organ.json` exists), the organ runs every time the spark fires.

## Zero Organs

If no organs are configured, the spark exits cleanly with code 0. This is by design — you can install the cron job first, then add organs later.
