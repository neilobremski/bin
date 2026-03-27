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

**Prerequisites:** bash, flock, jq.

---

## Architecture Overview

The organism treats autonomous programs as **organs** within **body parts** (containers, VMs, or local environments). Organs are portable, self-contained, and infrastructure-agnostic -- swap MQTT for local files, S3 for a shared folder, without changing a single line of organ code. A body part needs only a **filesystem** and **program execution**.

### The Three Layers

| Layer | Name | Purpose |
|-------|------|---------|
| **0** | **Spark** (Metabolism) | Lifecycle and scheduling. Cooldown-based firing, `flock` concurrency. |
| **1** | **Stimulus** (Nervous System) | Async signaling. Low-latency nerve impulses between organs via **ganglion** routing. |
| **2** | **Circ** (Circulatory System) | Data transport. Content-addressed blobs moved between body parts via **artery** caching. |

### The CLI Contract

Organs interact with all layers through three CLI tools on `$PATH`: `stimulus`, `circ`, and `spark-one`. Swap infrastructure (MQTT to local files, S3 to shared volume) without changing organ code.

---

## The Spark (Layer 0)

The spark manages organ lifecycle: an organ runs **if and only if** it is not already running and its cooldown has been met. Every organ is a directory containing `live` -- the universal entry point.

### Three Spark Drivers

**`spark-cron` (Standard)** -- Triggered by `crontab` once per minute. Iterates `$ORGANS` (colon-delimited list of organ paths) and sparks each organ whose cooldown is met.

**`spark-loop` (Fast cycle)** -- A `while true` loop with configurable sleep. Usage: `spark-loop <sleep-seconds>`.

**`spark-one` (Immediate)** -- Targets a single organ by name for immediate execution. Resolves the organ path by searching `$ORGANS`. Used by the ganglion to excite an organ when a stimulus arrives. Excitation is **best-effort**: `flock -n` silently skips if the organ is already running. Exits non-zero if the organ name is not found in `$ORGANS`.

### Concurrency: `flock`

All spark drivers use `flock -n` on `<organ_dir>/.lock`. If the lock is held, the spark silently exits -- no duplicate processes.

### Cooldown

An organ defines its rate via a `cooldown` file (single integer). The spark tracks ticks in `<organ_dir>/.ticks`. Logic: if `tick >= cooldown`, fire and reset to 0; otherwise increment.

| Cooldown | Behavior | Ticks: 0 → 1 → 2 → 3 → 4 → 5 |
|----------|----------|-------------------------------|
| **1** | Every other tick | skip, **fire**, skip, **fire**, skip, **fire** |
| **3** | Every 4th tick | skip, skip, skip, **fire**, skip, skip |
| **0** | Every tick | **fire**, **fire**, **fire**, **fire**, **fire**, **fire** |

> **Note:** Cooldown is a threshold, not a frequency. Cooldown 0 means fire every tick (tick always meets threshold). Cooldown 1 means fire every *other* tick. Set cooldown to 0 for maximum firing rate.

```bash
# Core spark logic (production version backgrounds with &)
for organ_path in ${ORGANS//:/ }; do
  COOLDOWN=$(cat "$organ_path/cooldown" 2>/dev/null || echo 1)
  TICK_FILE="$organ_path/.ticks"
  CURRENT_TICK=$(cat "$TICK_FILE" 2>/dev/null || echo 0)

  if [ "$CURRENT_TICK" -ge "$COOLDOWN" ]; then
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

Returns exit `0` if handed to the ganglion (or written to the target's `.stimulus/` directory in the local mock). Returns non-zero if the target organ is not found in `$ORGANS`. Does not guarantee delivery.

When sparked, an organ checks `.stimulus/` for JSON files, processes them in **lexicographic order** (sorted by filename), and deletes each after processing.

### Stimulus Implementation Requirements

A `stimulus` implementation must:

1. Parse `send --to <name> --body '<json>'` from arguments.
2. Resolve the target organ's path by searching `$ORGANS` (colon-delimited) for a matching `basename`.
3. Create the target's `.stimulus/` directory if it does not exist.
4. Write the JSON body to a unique file in `.stimulus/` (use `mktemp` with `.json` suffix for lexicographic ordering).
5. Call `spark-one <organ_name>` to excite the target organ.
6. Exit non-zero if the target organ is not found.

### The Ganglion

The ganglion bridges the network bus (MQTT, etc.) to the local filesystem:

1. Read destination organ from stimulus header.
2. If target exists in local `$ORGANS`: write payload to `<organ>/.stimulus/` as JSON, call `spark-one <organ_name>`.
3. If not local: relay to the bus for other body parts.
4. If no match: drop the message.

Stimuli are buffered on disk -- if an organ is dormant, signals wait until it fires.

---

## The Circulatory System (Layer 2)

The circulatory system moves large data blobs between body parts using content-addressed storage.

### The `circ` CLI Contract

| Command | Returns |
|---------|---------|
| `circ push <path>` | Content hash to stdout. Stores in local cache, registers with remote relay. Non-zero exit if file not found. |
| `circ get <hash>` | Absolute file path to stdout. Non-zero exit if hash not found. |

Unknown commands must exit non-zero with usage information.

**Flow:** Organ A runs `circ push data.txt`, gets a hash, sends it via stimulus to Organ B. Organ B runs `circ get <hash>` and gets a local path.

### Circ Implementation Requirements

A `circ` implementation must:

1. **push**: Validate the file exists. Compute a content hash (algorithm is implementation-defined). Store the file content-addressed (atomic write: temp file + rename). Print the hash to stdout.
2. **get**: Look up the hash in local cache. If found, print the absolute path to stdout. If not found, exit non-zero with error to stderr.
3. Resolve the `.circulatory/` storage directory relative to the implementation's install location (not the caller's working directory), so all organs on the same body part share one cache.

Each body part runs an **artery** managing the local `.circulatory/` cache and syncing with the remote relay (S3, NATS, shared volume). Blobs are ephemeral -- TTLs and pruning prevent storage exhaustion.

---

## Organ Anatomy

### Directory Layout

```text
organ_name/
 |-- live             # Entry point (required, must be executable)
 |-- cooldown          # Firing threshold as integer (optional, defaults to 1)
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
2. **Digest** -- read `.stimulus/*.json` in sorted order. Parse each payload. On parse failure, log and delete the file.
3. **Process** -- run internal logic. Pull data with `circ get` if needed. Check exit codes.
4. **Respond** -- signal other organs with `stimulus send`. Check exit codes.
5. **Output** -- push new data with `circ push`, send hash via stimulus.
6. **Cleanup** -- delete each stimulus file only after it has been fully processed. Never delete before processing completes.
7. **Exit** -- process ends, `flock` lock released.

### Dependencies

Organs carry their own dependencies: `node_modules/` for Node.js, `venv/` for Python. Moving an organ = copying the directory. The host needs the runtime and CLI contracts on `$PATH`.

---

## Local Lab

The `local-lab/` directory contains a complete working mock of the organism. It replaces production infrastructure (MQTT, S3, cron) with local filesystem operations. No network required.

```text
local-lab/
 |-- local-lab.sh         # Resets state and runs the ping/pong demo
 |-- bin/
 |   |-- stimulus          # Mock nervous system (writes to .stimulus/, calls spark-one)
 |   |-- circ              # Mock circulatory system (content-addressed .circulatory/ dir)
 |   |-- spark-cron        # Mock spark (iterates $ORGANS, checks cooldown, fires organs)
 |   |-- spark-one         # Mock immediate excitation (fires one organ by name)
 |-- organs/
 |   |-- ping/live         # Pushes a payload via circ, sends hash via stimulus
 |   |-- pong/live         # Receives stimulus, pulls payload via circ, prints it
 |-- .circulatory/         # Content-addressed blob store (created at runtime)
```

The mock CLIs implement the contracts described above with these simplifications:

- **Synchronous execution.** Production spark backgrounds organs with `flock -n ... &`. The mocks run organs sequentially for deterministic output. An organ that sends a stimulus to itself will block in the local lab but work in production.
- **Local-only routing.** The mock stimulus resolves organ paths from `$ORGANS` and writes directly to the filesystem. No ganglion, no network bus.
- **No TTL/pruning.** The mock circ stores blobs indefinitely in `.circulatory/`. `local-lab.sh` cleans this directory on each run.

To run: `cd local-lab && bash local-lab.sh`

To build your own organ, copy `organs/ping/` as a template. Your `live` script must be executable, start with `cd "$(dirname "$0")"`, and interact only through `stimulus`, `circ`, and the filesystem.

---

## Error Handling

### `live` exits non-zero

The spark does not retry. The `flock` lock is released and the organ waits for its next cooldown tick (or next excitation). Organs should handle their own retries internally or write failure state to `.memory/`.

### `circ get` fails

Returns non-zero exit code. The organ should check the return code and handle gracefully -- skip processing, log the error, or write to `.memory/` for retry on next spark.

### `circ push` fails

Returns non-zero exit code if the file does not exist or cannot be stored. The organ should check the return code and bail rather than sending an empty hash downstream.

### Bad JSON in `.stimulus/`

Organs are responsible for validating stimulus payloads. If a file fails to parse, the organ should log the error, delete the file (to prevent re-processing), and continue with remaining stimuli.

### `stimulus send` fails

Returns non-zero if the target organ is not found in `$ORGANS` or if the ganglion rejected the message. The organ should check the exit code.

### Organ already running (flock contention)

`flock -n` is non-blocking. If the organ holds the lock, the spark silently exits. Stimuli remain on disk in `.stimulus/` until the next successful spark. No data is lost.
