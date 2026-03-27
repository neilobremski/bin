# Synthetic Organism: A Technical Field Manual
By Neil C. Obremski and Knobert Esquire

## Quickstart

```bash
cd organism/local-lab && bash local-lab.sh
# === Local Lab: ping/pong demo ===
# [ping] fired
# [ping] pushed payload: 6d9409f85229...
# [pong] retrieved 6d9409f85229...
# [pong] got: hello from ping at Fri Mar 27 09:18:26 AM PDT 2026
# === Done ===
```

**What just happened:** Ping wrote a message, pushed it into the circulatory system (`circ push`), and sent the content hash to pong via the nervous system (`stimulus send`). Pong received the hash, pulled the payload (`circ get`), and read the message. All three layers -- spark, stimulus, circ -- working together.

**Prerequisites:** bash, flock, jq, sha256sum.

### How it works

```text
local-lab/
 |-- bin/              # Mock CLIs (see Local Lab section)
 |-- organs/
 |   |-- ping/
 |   |   |-- live
 |   |   |-- cadence       # contains: 1
 |   |-- pong/
 |       |-- live
 |       |-- cadence       # contains: 1
 |-- .circulatory/         # content-addressed blob store
```

**`organs/ping/live`** -- pushes a payload and signals pong:

```bash
#!/bin/bash
cd "$(dirname "$0")"
echo "[ping] fired"

MSG_FILE=$(mktemp)
echo "hello from ping at $(date)" > "$MSG_FILE"
HASH=$(circ push "$MSG_FILE")
rm "$MSG_FILE"

if [ -z "$HASH" ]; then
  echo "[ping] circ push failed" >&2
  exit 1
fi

echo "[ping] pushed payload: ${HASH:0:12}..."
stimulus send --to pong --body "{\"hash\": \"$HASH\"}"
```

**`organs/pong/live`** -- pulls payloads from the circulatory system:

```bash
#!/bin/bash
cd "$(dirname "$0")"
shopt -s nullglob
files=(.stimulus/*.json)
shopt -u nullglob

for f in "${files[@]}"; do
  HASH=$(jq -r '.hash' "$f" 2>/dev/null)

  if [ -z "$HASH" ] || [ "$HASH" = "null" ]; then
    echo "[pong] stimulus missing hash, skipping"
    rm "$f"
    continue
  fi

  FILE_PATH=$(circ get "$HASH")
  if [ $? -ne 0 ]; then
    echo "[pong] circ get failed for ${HASH:0:12}..."
    rm "$f"
    continue
  fi

  rm "$f"
  echo "[pong] retrieved ${HASH:0:12}..."
  echo "[pong] got: $(cat "$FILE_PATH")"
done
```

---

## Architecture Overview

The organism treats autonomous programs as **organs** within **body parts** (containers, VMs, or local environments). Organs are portable, self-contained, and infrastructure-agnostic — swap MQTT for local files, S3 for a shared folder, without changing a single line of organ code. A body part needs only a **filesystem** and **program execution**.

### The Three Layers

| Layer | Name | Purpose |
|-------|------|---------|
| **0** | **Spark** (Metabolism) | Lifecycle and scheduling. Time-based cadence, `flock` concurrency. |
| **1** | **Stimulus** (Nervous System) | Async signaling. Low-latency nerve impulses between organs via **ganglion** routing. |
| **2** | **Circ** (Circulatory System) | Data transport. Content-addressed blobs (SHA-256) moved between body parts via **artery** caching. |

### The CLI Contract

Organs interact with all layers through three CLI tools on `$PATH`: `stimulus`, `circ`, and `spark-one`. Swap infrastructure (MQTT to local files, S3 to shared volume) without changing organ code.

---

## The Spark (Layer 0)

The spark manages organ lifecycle: an organ runs **if and only if** it is not already running and its cadence has been met. Every organ is a directory containing `live` -- the universal entry point.

### Three Spark Drivers

**`spark-cron` (Standard)** -- Triggered by `crontab` once per minute. Iterates `$ORGANS` and sparks each organ whose cadence is met.

**`spark-loop` (Fast cycle)** -- A `while true` loop with configurable sleep. Usage: `spark-loop <sleep-seconds>`.

**`spark-one` (Immediate)** -- Targets a single organ for immediate execution. Used by the ganglion to excite an organ when a stimulus arrives. Excitation is **best-effort**: `flock -n` silently skips if the organ is already running.

### Concurrency: `flock`

All spark drivers use `flock -n` on `<organ_dir>/.lock`. If the lock is held, the spark silently exits -- no duplicate processes.

### Cadence

An organ defines its rate via a `cadence` file (single integer). The spark tracks ticks in `<organ_dir>/.ticks`. Logic: if `tick >= cadence`, fire and reset to 0; otherwise increment.

| Cadence | Behavior | Ticks: 0 → 1 → 2 → 3 → 4 → 5 |
|---------|----------|-------------------------------|
| **1** | Every other tick | skip, **fire**, skip, **fire**, skip, **fire** |
| **3** | Every 4th tick | skip, skip, skip, **fire**, skip, skip |
| **0** | Every tick | **fire**, **fire**, **fire**, **fire**, **fire**, **fire** |

> **Note:** Cadence is a threshold, not a frequency. Cadence 0 means fire every tick (tick always meets threshold). Cadence 1 means fire every *other* tick. Set cadence to 0 for maximum firing rate.

```bash
# Core spark logic
for organ_path in ${ORGANS//:/ }; do
  CADENCE=$(cat "$organ_path/cadence" 2>/dev/null || echo 1)
  TICK_FILE="$organ_path/.ticks"
  CURRENT_TICK=$(cat "$TICK_FILE" 2>/dev/null || echo 0)

  if [ "$CURRENT_TICK" -ge "$CADENCE" ]; then
    echo "0" > "$TICK_FILE"
    LOCK_FILE="$organ_path/.lock"
    flock -n "$LOCK_FILE" -c "$organ_path/live" &
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
2. If target exists in local `$ORGANS`: write payload to `<organ>/.stimulus/` as JSON, call `spark-one <organ_name>`.
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
 |-- live             # Entry point (required, must be executable)
 |-- cadence           # Firing rate as integer (optional, defaults to 1)
 |-- src/              # Internal logic (any language)
 |-- .stimulus/        # Incoming signals (volatile, created by ganglion)
 |-- .memory/          # Persistent local state (non-critical, may not survive migration)
```

### Example `live` (Python)

```bash
#!/bin/bash
cd "$(dirname "$0")"
source ./venv/bin/activate
python3 src/main.py
```

### Digest Pattern

The standard organ cycle:

1. **Awaken** -- `live` triggered by spark.
2. **Digest** -- read `.stimulus/*.json` in sorted order, delete after processing.
3. **Process** -- run internal logic. Pull data with `circ get` if needed.
4. **Respond** -- signal other organs with `stimulus send`.
5. **Output** -- push new data with `circ push`, send hash via stimulus.
6. **Exit** -- process ends, `flock` lock released.

### Dependencies

Organs carry their own dependencies: `node_modules/` for Node.js, `venv/` for Python. Moving an organ = copying the directory. The host needs the runtime and CLI contracts on `$PATH`.

---

## Local Lab

These four scripts replace production infrastructure (MQTT, S3, cron) with local filesystem operations. Put them in `bin/` and add to your `$PATH`. No network required.

```bash
export ORGANS="./organs/brain:./organs/mouth"
export PATH="$(pwd)/bin:$PATH"
```

> **Important:** The local lab mocks run organs synchronously for deterministic output. In production, spark backgrounds organs with `flock -n ... &` and stimulus delivery is async via the ganglion. An organ that sends a stimulus to itself will block in the local lab but work in production.

### Mock `bin/stimulus`

Resolves the target organ from `$ORGANS`, writes the payload to `.stimulus/`, and fires the organ via `spark-one`.

```bash
#!/bin/bash
# Mock Nervous System
CMD=""
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR/.."

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

# Resolve organ path from $ORGANS
IFS=':' read -ra ADDR <<< "$ORGANS"
ORGAN_PATH=""
for path in "${ADDR[@]}"; do
  if [[ $(basename "$path") == "$TARGET" ]]; then
    ORGAN_PATH="$path"
    break
  fi
done

if [ -z "$ORGAN_PATH" ]; then
  echo "stimulus: organ not found: $TARGET" >&2
  exit 1
fi

STIM_DIR="$ORGAN_PATH/.stimulus"
mkdir -p "$STIM_DIR"
TMPFILE=$(mktemp "$STIM_DIR/XXXXXX.json")
echo "$BODY" > "$TMPFILE"

spark-one "$TARGET"
```

### Mock `bin/circ`

Content-addressed blob store backed by a local `.circulatory/` directory. Atomic writes via tmp+mv.

```bash
#!/bin/bash
# Mock Circulatory System
CMD=$1
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
HEART_DIR="$DIR/../.circulatory"
mkdir -p "$HEART_DIR"

if [ "$CMD" == "push" ]; then
  FILE_PATH=$2
  if [ ! -f "$FILE_PATH" ]; then
    echo "circ push: file not found: $FILE_PATH" >&2
    exit 1
  fi
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
else
  echo "Usage: circ push <path> | circ get <hash>" >&2
  exit 1
fi
```

### Mock `bin/spark-cron`

```bash
#!/bin/bash
# Mock Spark (Cron)
# Note: runs organs sequentially (production backgrounds them)
IFS=':' read -ra ADDR <<< "$ORGANS"

for organ_path in "${ADDR[@]}"; do
  CADENCE=$(cat "$organ_path/cadence" 2>/dev/null || echo 1)
  TICK_FILE="$organ_path/.ticks"
  CURRENT_TICK=$(cat "$TICK_FILE" 2>/dev/null || echo 0)

  if [ "$CURRENT_TICK" -ge "$CADENCE" ]; then
    echo "0" > "$TICK_FILE"
    LOCK_FILE="$organ_path/.lock"
    flock -n "$LOCK_FILE" -c "$organ_path/live"
  else
    echo $((CURRENT_TICK + 1)) > "$TICK_FILE"
  fi
done
```

### Mock `bin/spark-one`

```bash
#!/bin/bash
# Immediate Excitation (best-effort: silently skips if organ is busy)
# Note: runs synchronously in mock (production backgrounds with &)
ORGAN_NAME=$1
IFS=':' read -ra ADDR <<< "$ORGANS"

for path in "${ADDR[@]}"; do
  if [[ $(basename "$path") == "$ORGAN_NAME" ]]; then
    LOCK_FILE="$path/.lock"
    flock -n "$LOCK_FILE" -c "$path/live"
    exit 0
  fi
done

echo "spark-one: organ not found: $ORGAN_NAME" >&2
exit 1
```

---

## Error Handling

### `live` exits non-zero

The spark does not retry. The `flock` lock is released and the organ waits for its next cadence tick (or next excitation). Organs should handle their own retries internally or write failure state to `.memory/`.

### `circ get` fails

Returns non-zero exit code. The organ should check the return code and handle gracefully -- skip processing, log the error, or write to `.memory/` for retry on next spark.

### Bad JSON in `.stimulus/`

Organs are responsible for validating stimulus payloads. If a file fails to parse, the organ should log the error, delete the file (to prevent re-processing), and continue with remaining stimuli.

### `stimulus send` fails

Returns non-zero if the ganglion rejected the message. In the local lab mock, this only happens if the `send` subcommand is missing. The organ should check the exit code.

### Organ already running (flock contention)

`flock -n` is non-blocking. If the organ holds the lock, the spark silently exits. Stimuli remain on disk in `.stimulus/` until the next successful spark. No data is lost.
