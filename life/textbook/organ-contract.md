# Organ Contract

An organ is a directory with an executable `live.sh`. Organs can live anywhere — different directories, repos, or machines.

## Required: live.sh

The spark launches this script. It must be executable. The organ does its work and exits.

```bash
#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Do the organ's work here ---

# Optionally report health when done
echo "ok cycle complete" > "$DIR/health.txt"
```

The spark enforces singleton via `flock` — the organ does not need to manage its own locking. Language-agnostic: `live.sh` can exec into Python, Node, or anything.

## Optional: organ.json

Cadence configuration. If missing, the organ runs every spark cycle.

```json
{
  "cadence": 5
}
```

`cadence` is minutes between launches. Only field the spark reads.

## Optional: health.txt

Status reporting for monitoring and future immune-system use. The spark does not read this.

```
ok processed 42 items
```

First word is the status: `ok`, `degraded`, or `error`. Rest is a human-readable message. The file's modification time is the timestamp.

## Optional: stimulus.jsonl

Per-organ input queue. See [stimulus.md](stimulus.md).

## Spark-Managed Files

The spark creates these — organs should not touch them:

| File | Purpose |
|------|---------|
| `.spark.last` | Epoch seconds of last launch (cadence check) |
| `.spark.log` | stdout/stderr captured from `live.sh` |
