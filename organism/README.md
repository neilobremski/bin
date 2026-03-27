# Synthetic Organism: A Technical Field Manual
By Neil C. Obremski and Knobert Esquire

## Quickstart: Two Organs in 60 Seconds

```text
organism/
 |-- bin/              # Mock CLIs (see Local Lab section)
 |-- organs/
 |   |-- ping/
 |   |   |-- live.sh
 |   |   |-- cadence       # contains: 1
 |   |-- pong/
 |       |-- live.sh
 |       |-- cadence       # contains: 1
 |-- .circulatory/
 |-- .ticks/
```

**`organs/ping/live.sh`** -- sends a stimulus to pong:

```bash
#!/bin/bash
cd "$(dirname "$0")"
echo "[ping] fired"
stimulus send --to pong --body '{"msg": "hello from ping"}'
```

**`organs/pong/live.sh`** -- digests stimuli in sorted order:

```bash
#!/bin/bash
cd "$(dirname "$0")"
for f in $(ls .stimulus/*.json 2>/dev/null | sort); do
  echo "[pong] got: $(cat "$f")"
  rm "$f"
done
```

Run it (after creating mock CLIs from the Local Lab section):

```bash
chmod +x organs/*/live.sh
export ORGANS="./organs/ping:./organs/pong"
export PATH="$PATH:$(pwd)/bin"
spark-cron.sh        # tick 0 -> increments to 1
spark-cron.sh        # tick 1 >= cadence 1 -> fires both organs
# [ping] fired
# [pong] got: {"msg": "hello from ping"}
```

---

## Architecture Overview

The organism treats autonomous programs as **organs** within **body parts** (containers, VMs, or local environments). A body part needs only a **filesystem** and **program execution**.

### The Three Layers

| Layer | Name | Purpose |
|-------|------|---------|
| **0** | **Spark** (Metabolism) | Lifecycle and scheduling. Time-based cadence, `flock` concurrency. |
| **1** | **Stimulus** (Nervous System) | Async signaling. Low-latency nerve impulses between organs via **ganglion** routing. |
| **2** | **Circ** (Circulatory System) | Data transport. Content-addressed blobs (SHA-256) moved between body parts via **artery** caching. |

### The CLI Contract

Organs interact with all layers through three CLI tools on `$PATH`: `stimulus`, `circ`, and `spark-one.sh`. Swap infrastructure (MQTT to local files, S3 to shared volume) without changing organ code.

---

## The Spark (Layer 0)

The spark manages organ lifecycle: an organ runs **if and only if** it is not already running and its cadence has been met. Every organ is a directory containing `live.sh` -- the universal entry point.

### Three Spark Drivers

**`spark-cron.sh` (Standard)** -- Triggered by `crontab` once per minute. Iterates `$ORGANS` and sparks each organ whose cadence is met.

**`spark-loop.sh` (Fast cycle)** -- A `while true` loop with configurable sleep. Usage: `spark-loop.sh <sleep-seconds>`.

**`spark-one.sh` (Immediate)** -- Targets a single organ for immediate execution. Used by the ganglion to excite an organ when a stimulus arrives. Excitation is **best-effort**: `flock -n` silently skips if the organ is already running.

### Concurrency: `flock`

All spark drivers use `flock -n` on `/tmp/organ_<name>.lock`. If the lock is held, the spark silently exits -- no duplicate processes.

### Cadence

An organ defines its rate via a `cadence` file (single integer). The spark tracks ticks in `/tmp/<organ_name>.tick` (or `.ticks/` locally). Logic uses 0-based counting with `>=`: if `tick >= cadence`, fire and reset to 0; otherwise increment. With `cadence=1`, the organ fires every other tick. With `cadence=5`, every 5th.

```bash
# Core spark logic
for organ_path in ${ORGANS//:/ }; do
  organ_name=$(basename "$organ_path")
  CADENCE=$(cat "$organ_path/cadence" 2>/dev/null || echo 1)
  TICK_FILE="/tmp/organ_$organ_name.tick"
  CURRENT_TICK=$(cat "$TICK_FILE" 2>/dev/null || echo 0)

  if [ "$CURRENT_TICK" -ge "$CADENCE" ]; then
    echo "0" > "$TICK_FILE"
    LOCK_FILE="/tmp/organ_$organ_name.lock"
    flock -n "$LOCK_FILE" -c "$organ_path/live.sh" &
  else
    echo $((CURRENT_TICK + 1)) > "$TICK_FILE"
  fi
done
```

---

## The Nervous System (Layer 1)

The nervous system carries **intent** -- small async signals between organs.

### The `stimulus` CLI Contract

```bash
stimulus send --to <organ_name> --body '<json_payload>'
```

Returns exit `0` if handed to the ganglion. Does not guarantee delivery. When sparked, an organ checks `.stimulus/` for JSON files, processes them in **lexicographic order** (sorted by filename), and deletes each after processing.

### The Ganglion

The ganglion bridges the network bus (MQTT, etc.) to the local filesystem:

1. Read destination organ from stimulus header.
2. If target exists in local `$ORGANS`: write payload to `<organ>/.stimulus/` as JSON, call `spark-one.sh <organ>`.
3. If not local: relay to the bus for other body parts.
4. If no match: drop the message.

Stimuli are buffered on disk -- if an organ is dormant, signals wait until it fires.

---

## The Circulatory System (Layer 2)

The circulatory system moves large data blobs between body parts using content-addressed storage (SHA-256).

### The `circ` CLI Contract

| Command | Returns |
|---------|---------|
| `circ push <path>` | SHA-256 hash to stdout. Stores in local cache, registers with remote relay. |
| `circ get <hash>` | Absolute file path to stdout. Non-zero exit on failure. |
| `circ status` | Connection health string. |

**Flow:** Organ A runs `circ push data.txt`, gets a hash, sends it via stimulus to Organ B. Organ B runs `circ get <hash>` and gets a local path.

Each body part runs an **artery** managing the local `.circulatory/` cache and syncing with the remote relay (S3, NATS, shared volume). Blobs are ephemeral -- TTLs and pruning prevent storage exhaustion.

---

## Organ Anatomy

### Directory Layout

```text
organ_name/
 |-- live.sh          # Entry point (required, must be executable)
 |-- cadence           # Firing rate as integer (optional, defaults to 1)
 |-- src/              # Internal logic (any language)
 |-- .stimulus/        # Incoming signals (volatile, created by ganglion)
 |-- .memory/          # Persistent local state (non-critical, may not survive migration)
```

### Example `live.sh` (Python)

```bash
#!/bin/bash
cd "$(dirname "$0")"
source ./venv/bin/activate
python3 src/main.py
```

### Digest Pattern

The standard organ cycle:

1. **Awaken** -- `live.sh` triggered by spark.
2. **Digest** -- read `.stimulus/*.json` in sorted order, delete after processing.
3. **Process** -- run internal logic. Pull data with `circ get` if needed.
4. **Respond** -- signal other organs with `stimulus send`.
5. **Output** -- push new data with `circ push`, send hash via stimulus.
6. **Exit** -- process ends, `flock` lock released.

### Dependencies

Organs carry their own dependencies: `node_modules/` for Node.js, `venv/` for Python. Moving an organ = copying the directory. The host needs the runtime and CLI contracts on `$PATH`.

---

## Local Lab

Replace production binaries with bash mocks for local development with no network.

```bash
export ORGANS="./organs/brain:./organs/mouth"
export PATH="$PATH:$(pwd)/bin"
```

### Mock `bin/stimulus`

```bash
#!/bin/bash
# Mock Nervous System
CMD=""
while [[ $# -gt 0 ]]; do
  case $1 in
    send) CMD="send"; shift ;;
    --to) TARGET="$2"; shift 2 ;;
    --body) BODY="$2"; shift 2 ;;
    *) shift ;;
  esac
done

if [ "$CMD" != "send" ]; then
  echo "Usage: stimulus send --to <organ> --body '<json>'" >&2
  exit 1
fi

STIM_DIR="./organs/$TARGET/.stimulus"
mkdir -p "$STIM_DIR"
TMPFILE=$(mktemp "$STIM_DIR/XXXXXX.json")
echo "$BODY" > "$TMPFILE"

spark-one.sh "$TARGET"
```

### Mock `bin/circ`

```bash
#!/bin/bash
# Mock Circulatory System
CMD=$1
HEART_DIR="./.circulatory"
mkdir -p "$HEART_DIR"

if [ "$CMD" == "push" ]; then
  FILE_PATH=$2
  HASH=$(sha256sum "$FILE_PATH" | awk '{print $1}')
  TMPFILE=$(mktemp "$HEART_DIR/.tmp.XXXXXX")
  cp "$FILE_PATH" "$TMPFILE"
  mv "$TMPFILE" "$HEART_DIR/$HASH"
  echo "$HASH"
elif [ "$CMD" == "get" ]; then
  HASH=$2
  if [ -f "$HEART_DIR/$HASH" ]; then
    echo "$(realpath "$HEART_DIR/$HASH")"
  else
    echo "circ get: hash not found: $HASH" >&2
    exit 1
  fi
fi
```

### Mock `bin/spark-cron.sh`

```bash
#!/bin/bash
# Mock Spark (Cron)
IFS=':' read -ra ADDR <<< "$ORGANS"
TICK_DIR="./.ticks"
mkdir -p "$TICK_DIR"

for organ_path in "${ADDR[@]}"; do
  organ_name=$(basename "$organ_path")
  CADENCE=$(cat "$organ_path/cadence" 2>/dev/null || echo 1)
  TICK_FILE="$TICK_DIR/$organ_name"
  CURRENT_TICK=$(cat "$TICK_FILE" 2>/dev/null || echo 0)

  if [ "$CURRENT_TICK" -ge "$CADENCE" ]; then
    echo "0" > "$TICK_FILE"
    spark-one.sh "$organ_name"
  else
    echo $((CURRENT_TICK + 1)) > "$TICK_FILE"
  fi
done
```

### Mock `bin/spark-one.sh`

```bash
#!/bin/bash
# Immediate Excitation (best-effort: silently skips if organ is busy)
ORGAN_NAME=$1
IFS=':' read -ra ADDR <<< "$ORGANS"

for path in "${ADDR[@]}"; do
  if [[ $(basename "$path") == "$ORGAN_NAME" ]]; then
    LOCK_FILE="/tmp/organ_$ORGAN_NAME.lock"
    flock -n "$LOCK_FILE" -c "$path/live.sh" &
    exit 0
  fi
done
```

---

## Error Handling

### `live.sh` exits non-zero

The spark does not retry. The `flock` lock is released and the organ waits for its next cadence tick (or next excitation). Organs should handle their own retries internally or write failure state to `.memory/`.

### `circ get` fails

Returns non-zero exit code. The organ should check the return code and handle gracefully -- skip processing, log the error, or write to `.memory/` for retry on next spark.

### Bad JSON in `.stimulus/`

Organs are responsible for validating stimulus payloads. If a file fails to parse, the organ should log the error, delete the file (to prevent re-processing), and continue with remaining stimuli.

### `stimulus send` fails

Returns non-zero if the ganglion rejected the message. In the local lab mock, this only happens if the `send` subcommand is missing. The organ should check the exit code.

### Organ already running (flock contention)

`flock -n` is non-blocking. If the organ holds the lock, the spark silently exits. Stimuli remain on disk in `.stimulus/` until the next successful spark. No data is lost.
