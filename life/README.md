# Life Spark

A cron-based heartbeat that launches "organs" — autonomous processes that do work on their own schedule. Pure bash, no dependencies beyond coreutils. Works on Linux, macOS, and WSL.

The spark is deliberately dumb: discover organs, check if they're due, launch them. All intelligence lives in the organs themselves.

## Install to Cron

The easiest way: put an `organs.conf` in your home directory and source `install.sh`. It will install the cron job automatically. Or manually:

```
* * * * * /path/to/life/spark-cron.sh
```

`spark-cron.sh` wraps `spark.sh` with daily log rotation (`~/.organs/spark-YYYY-MM-DD.log`, 7-day retention).

## Configuring Organs

The spark discovers organ directories in priority order:

1. **Argument** — pass a manifest file path: `spark.sh /path/to/manifest.conf`
2. **Environment** — set `ORGANS` as colon-separated paths (like `PATH`): `ORGANS=/opt/brain:/opt/eyes spark.sh`
3. **Working directory** — `organs.conf` in the current directory (`cd my-organism && spark.sh`)
4. **Home manifest** — `~/organs.conf` (same format — ideal for machine-wide config with zero args)

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
| `.spark.last` | Epoch seconds of last launch (cadence check) |
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

## health.json (optional)

Organs can write a `health.json` file in their directory to report their status. The spark does not read this file (yet) — it's a contract for future monitoring / immune system use.

```json
{
  "status": "ok",
  "ts": "2026-03-16T12:00:00-07:00",
  "message": "processed 42 items"
}
```

| Field | Type | Values |
|-------|------|--------|
| `status` | string | `ok`, `degraded`, or `down` |
| `ts` | string | ISO-8601 timestamp |
| `message` | string | Optional human-readable note |
