# Life Spark

A cron-based heartbeat that launches organs. Pure bash, no dependencies beyond coreutils.

## Quick Start

```bash
# 1. Create life.conf in your organism directory
echo "ORGANS=organs/heart" > life.conf

# 2. Run the spark
/path/to/life/spark.sh

# 3. Install to cron for continuous heartbeat
#    * * * * * cd /path/to/organism && /path/to/life/spark-cron.sh
```

## How It Works

| Concept | One-liner |
|---------|-----------|
| **life.conf** | Sourceable shell file. `ORGANS` (colon-separated) plus any env vars organs need. |
| **Organ** | A directory with an executable `live.sh`. That's it. |
| **Cadence** | Optional `organ.conf` with `CADENCE=N` (minutes). No file = every cycle. |
| **Singleton** | Spark enforces via `flock` in `~/.life/locks/`. Organs don't manage locking. |
| **Health** | Optional `health.txt`. First word = status (`ok`, `degraded`, `error`). |
| **Stimulus** | Optional `stimulus.txt`. Lines appended by the nervous system, read by the organ. |
| **Logs** | `spark-cron.sh` logs to `~/.life/spark/YYYY-MM-DD.log` (7-day retention). |

## Textbook

Full specifications live in [life/textbook/](textbook/):

- [spark.md](textbook/spark.md) — Layer 0 algorithm, life.conf, flock singleton
- [layers.md](textbook/layers.md) — The 3-layer model (Spark → Ganglion → Organs)
- [organ-contract.md](textbook/organ-contract.md) — What an organ must provide
- [stimulus.md](textbook/stimulus.md) — How signals reach organs

## Tadpole

The [tadpole](../tadpole/) is a minimal test organism with a single heart organ. Run `tadpole/lifetime.sh` to verify the life system works end-to-end.
