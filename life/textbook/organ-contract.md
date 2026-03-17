# Organ Contract

Every organ lives in its own directory under `organs/<name>/`.
This document defines the interface that all organs MUST implement.

## Required Files

### live.sh -- Entry Point

The spark launches this script. It must be executable. Conventions:

```bash
#!/usr/bin/env bash
set -euo pipefail

ORGAN_DIR="$(cd "$(dirname "$0")" && pwd)"
ORGAN_NAME="$(basename "$ORGAN_DIR")"

# Singleton guard (flock — exit 0 if already running, not an error)
exec 9>"$ORGAN_DIR/.spark.lock"
flock -n 9 || { echo "Already running"; exit 0; }

# --- Do the organ's actual work here ---

# Report health when done
cat > "$ORGAN_DIR/health.json" <<EOF
{"status":"ok","ts":"$(date -Iseconds)","message":"cycle complete"}
EOF
```

**Rules**:
- `set -euo pipefail` -- fail fast, fail loud
- `flock` for singleton enforcement -- exit 0 if already running (not an error). The FD number is arbitrary.
- Exit 0 on success, non-zero on failure
- Language-agnostic: `live.sh` can exec into Python, Node, or anything via its shebang

## Optional Files

### organ.json -- Manifest

Declares what the organ is and how the spark should manage it.
If missing, the organ runs every spark cycle with no cadence gating.

```json
{
  "name": "brain",
  "description": "Central decision-maker and orchestrator",
  "cadence": 5
}
```

**Field definitions**:

| Field | Type | Description |
|-------|------|-------------|
| cadence | int | Minutes between sparks. If missing, organ runs every cycle. |
| name | string | Human-readable organ identifier |
| description | string | Human-readable purpose |
| co_locate | list[string] | **[Planned]** Organ names that must share the same host |

Only `cadence` affects spark behavior. All other fields are informational
or used by the organ itself.

### health.json -- Self-Reported Status

Written by the organ itself. The spark does not currently read this file;
it exists for monitoring and future immune-system use.

```json
{
  "status": "ok",
  "ts": "2026-03-16T14:30:00-07:00",
  "message": "processed 42 items"
}
```

| Field | Type | Description |
|-------|------|-------------|
| status | string | One of: `ok`, `degraded`, `down` |
| ts | string | ISO 8601 timestamp |
| message | string | Optional human-readable note |

### stimulus.jsonl -- Input Queue

Per-organ stimulus file. See [stimulus.md](stimulus.md) for the full spec.
Written by the spinal cord (Layer 1) or other systems. Read by the organ.

### CLAUDE.md -- Identity DNA

For LLM-based organs only. Defines the organ's personality, role, and
behavioral constraints. The brain has one; a pure-Python organ does not need one.
