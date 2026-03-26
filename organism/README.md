# Synthetic Organism Biology: A Technical Field Manual

## Table of Contents

- [Overview: The Cellular Architecture](#overview-the-cellular-architecture)
- [Chapter 1: Layer 0 - Metabolism (The Spark)](#chapter-1-layer-0---metabolism-the-spark)
- [Chapter 2: Layer 1 - Nervous System (The Ganglion)](#chapter-2-layer-1---nervous-system-the-ganglion)
- [Chapter 3: Layer 2 - Circulatory System (The Artery)](#chapter-3-layer-2---circulatory-system-the-artery)
- [Chapter 4: The CLI Contract (Interfacing)](#chapter-4-the-cli-contract-interfacing)
- [Chapter 5: Organ Anatomy (Implementation Guide)](#chapter-5-organ-anatomy-implementation-guide)
- [Chapter 6: Local Lab (Mocking and Testing)](#chapter-6-local-lab-mocking-and-testing)

## Overview: The Cellular Architecture

The **Synthetic Organism** is a framework for distributed computing that treats autonomous programs as Organs. These organs reside within **Body Parts** (isolated containers, virtual machines, or local environments) and interact through three decoupled layers of existence.

The fundamental requirement for a Body Part to host life is minimal: it must be capable of **accessing a local filesystem** and **executing programs**.

### The Unit of Life: Organs-as-Programs

Every functional capability in the organism is an **Organ** -- a standalone executable. Whether a simple Bash script or a complex Python-based LLM interface, an organ is defined by its role and its adherence to the system's interface. Organs are inherently decoupled; they do not possess networking logic or storage drivers. They interact with the organism solely through **CLI Contracts**.

### The Three Layers of Existence

- **Metabolism (Layer 0): The Spark** The Spark manages the lifecycle and "Metabolic Rate" of the organs. It is a localized process that uses time-based cycles and file-locking (`flock`) to ensure organs run only when needed (**Cadence**) or when triggered by external excitation. This layer ensures the organism is resource-efficient, staying dormant until work is required.

- **Nervous System (Layer 1): The Spinal Cord** This is the signaling abstraction. While often implemented via protocols like **MQTT**, the **Spinal Cord** is conceptually any bus that allows a **Ganglion** (local proxy) to route asynchronous stimuli. It facilitates long-range communication between isolated body parts, allowing the "head" of the organism to signal a "limb" across network boundaries.

- **Circulatory System (Layer 2): The Heart** The **Heart** is the transport abstraction for high-bandwidth data (**Blobs**). The specific implementation -- be it **NATS**, **S3**, **Google Drive**, or a local shared volume -- is irrelevant to the organ. The Heart allows an **Artery** to circulate content-addressed data via **SHA-256 hashes**. It ensures that data is ephemeral and mobile, moving to where it is needed without filling up the storage of any single body part.

### The CLI Contract: The Universal Interface

The "truth" of the organism lives in the **CLI Contract**. Organs interact with the Nervous and Circulatory systems through standardized commands (`stimulus`, `circ`, `spark`). This decoupling provides three critical advantages:

1. **Infrastructure Agnosticism:** You can swap the "Heart" from a cloud-native Object Store to a local mock for development without modifying a single line of organ code.
2. **Bypass Isolation:** The contracts allow organs to communicate through a central broker/relay, punching through firewalls and container isolation.
3. **Metabolic Resilience:** If the Spinal Cord or Heart becomes unreachable, the local Metabolism (Spark) continues to function, allowing organs to process local tasks until the connection is restored.

---

## Chapter 1: Layer 0 -- Metabolism (The Spark)

Metabolism is the base layer of the organism. It governs the transition from static code (potential energy) to active processes (kinetic energy). Without a functioning Spark, the nervous and circulatory systems have no medium through which to act.

The Spark ensures that an organ runs **if and only if** it is not already running and its required **Cadence** has been met.

---

### 1.1 The Metabolic Entry Point: `live.sh`

Every organ is a directory identified by its name. Within that directory, the file `live.sh` serves as the universal entry point. Whether the organ is written in Python, Node.js, or C#, the `live.sh` script acts as the cell membrane, encapsulating the internal complexity and presenting a standard execution interface to the Spark.

### 1.2 The Three Spark Drivers

The organism utilizes three specific CLI programs to drive metabolism, depending on the environment and the required temporal resolution.

**A. `spark-cron.sh` (The Circadian Pulse)**

This is the standard driver for a stable organism. It is designed to be triggered by a system-level scheduler (like `crontab`) once per minute.

- **Role:** It iterates through the `$ORGANS` path and attempts to spark every recognized organ.
- **Usage:** Typically installed as `* * * * * /path/to/organism/bin/spark-cron.sh`.

**B. `spark-loop.sh` (Hyper-Metabolism)**

In environments where a one-minute resolution is too slow, or during active development, `spark-loop.sh` provides a continuous heartbeat.

- **Role:** A simple `while true` loop that calls the spark logic, sleeps for a few seconds, and repeats.
- **Usage:** Run manually in a terminal or as a managed background service to keep the organism "awake" and highly responsive.

**C. `spark-one.sh` (Adrenaline / Excitation)**

This is the manual or programmatic override. It targets a specific organ for immediate execution.

- **Role:** It bypasses the standard cron schedule. It is primarily used by the **Ganglion** (Nervous System) to "excite" a dormant organ the moment a stimulus arrives.
- **Usage:** `spark-one.sh mail_processor`.

---

### 1.3 Logic and Concurrency: `flock`

To prevent "Auto-Immune" overlap -- where multiple instances of the same organ compete for the same local resources -- all three Spark drivers rely on `flock`.

Before execution, the Spark attempts to acquire a non-blocking lock on a file representing that organ (usually in `/tmp/`).

- If the lock is acquired: The `live.sh` script is executed.
- If the lock fails: The Spark silently exits, acknowledging that the organ is already "metabolizing."

### 1.4 Cadence and The Tick

Not every organ needs to run every minute. An organ defines its "metabolic rate" by placing a `cadence` file in its directory.

- **Calculation:** The Spark maintains a "tick" counter for each organ in a temporary state directory.
- **Logic:** 1. If `current_tick >= cadence_value`, the organ is sparked and the tick is reset to `0`. 2. Otherwise, the tick is incremented and the organ remains dormant.

This mechanism allows the organism to host dozens of organs -- from high-frequency monitors (`cadence: 1`) to daily cleanup tasks (`cadence: 1440`) -- without saturating the host's CPU.

---

### 1.5 Implementation Example (Bash)

The following is a succinct representation of the logic contained within the Spark drivers:

```bash
# Core Spark Logic Snippet
for organ_path in ${ORGANS//:/ }; do
  organ_name=$(basename "$organ_path")
  LOCK_FILE="/tmp/organ_$organ_name.lock"

  # Check cadence (Layer 0 logic)
  if [[ $(should_spark "$organ_name") == "true" ]]; then
    # Concurrency check
    flock -n "$LOCK_FILE" -c "$organ_path/live.sh" &
  fi
done
```

This metabolic layer ensures that the organism is **pull-based** and **resource-aware**, a critical requirement for running on shared or ephemeral infrastructure.

---

## Chapter 2: Layer 1 -- Nervous System (The Ganglion)

If Metabolism is the energy of the organism, the **Nervous System** is its intent. It is the signaling plane responsible for low-latency, asynchronous communication. In this layer, information is treated as a **Stimulus** -- a brief pulse of data intended to trigger a specific reaction in an Organ.

The Nervous System allows an organism to coordinate across body parts that are physically isolated by firewalls or container boundaries.

---

### 2.1 The Spinal Cord: Global Signaling

The **Spinal Cord** is the abstract central bus of the organism. While typically implemented using a protocol like **MQTT**, its primary function is to act as a persistent relay. It does not store data; it simply ensures that a stimulus published by a "Hand" organ reaches the "Brain" organ, regardless of where those organs are currently metabolizing.

### 2.2 The Ganglion: The Local Nerve Center

In biological systems, a ganglion is a cluster of nerve cells that processes local reflexes. In our architecture, the **Ganglion** is a specialized, always-on Organ. It is the only component that maintains a persistent connection to the Spinal Cord.

**The Ganglion's Responsibilities:**

- **Persistent Listening:** It holds the connection "open" so that ephemeral organs don't have to.
- **Signal Translation:** It converts network packets into local filesystem events.
- **Local Discovery:** It uses the `$ORGANS` environment variable to "know" which parts of the body are currently attached to the local host.

---

### 2.3 The Excitatory Path (Routing)

When a Stimulus arrives at a Ganglion, a deterministic routing logic -- the **Reflex** -- is triggered:

| Step | Action | Logic |
|------|--------|-------|
| **1. Identification** | Target Check | The Ganglion reads the destination organ name from the stimulus header. |
| **2. Locality** | Map Lookup | It checks if the target organ exists within the local `$ORGANS` paths. |
| **3. Delivery** | Stimulation | If local, it writes the stimulus payload to the organ's `./.stimulus/` directory as a JSON file. |
| **4. Activation** | Excitation | It immediately calls `spark-one.sh [organ_name]` to force a metabolic spike. |
| **5. Propagation** | Broadcast | If the target is *not* local, the Ganglion (optionally) relays the signal back to the Spinal Cord for other body parts. |

### 2.4 The `stimulus` CLI Contract

To keep organs agnostic of the networking stack, they interact with the Nervous System exclusively through the `stimulus` CLI.

- **`stimulus send --to [organ_name] --body [json_payload]`**: An organ calls this to "fire" a nerve impulse. It doesn't need to know where the target is; the local Ganglion handles the transmission.
- **Digest Mode**: When an organ is sparked (Metabolism), it checks its `./.stimulus/` folder. If files exist, it "digests" them and then deletes them, clearing the synapse for the next signal.

---

### 2.5 Decoupling and Resilience

By using the Ganglion as a buffer, the organism gains **Signal Persistence**. If a "Mail" organ is dormant to save resources, the Ganglion collects incoming stimuli and holds them. The moment the "Mail" organ is sparked -- either by its natural cadence or by the Ganglion's `spark-one.sh` adrenaline shot -- the data is ready and waiting on the disk.

This architecture ensures that even if the Spinal Cord (network) is momentarily severed, the local "Reflexes" (Ganglion to local Organs) continue to function unimpeded.

---

## Chapter 3: Layer 2 -- Circulatory System (The Artery)

While the Nervous System carries the "intent" of the organism, the **Circulatory System** carries its "substance." It is the data plane responsible for moving high-bandwidth payloads -- referred to as **Blobs** -- between isolated body parts. In this layer, data is not a message to be read; it is a resource to be consumed, stored, and eventually recycled.

---

### 3.1 The Heart: The Central Relay

The **Heart** is the abstract central pump of the organism. Its role is to facilitate the exchange of Blobs between Arteries that cannot see each other directly.

Whether the Heart is implemented via **NATS (Synadia)**, **S3**, **Google Drive**, or a **Shared Volume**, its function remains the same: it acts as a temporary vessel. The Heart does not intend to be a permanent archive; it is a high-speed relay that enables "circulation" across the organism's distributed anatomy.

### 3.2 The Artery: The Local Vessel and Cache

Each body part hosts an **Artery** -- a specialized process or sidecar that manages the local movement of Blobs. The Artery is the bridge between the organ's local filesystem and the global Heart.

**The Artery's Responsibilities:**

- **Hashing:** It identifies every Blob by its **SHA-256 hash**, ensuring data integrity and deduplication.
- **Buffering:** It manages a local `./.circulatory/` cache to provide organs with near-instant access to frequently used data.
- **Transmission:** It handles the complex logic of chunking and uploading data to the Heart and fetching missing Blobs on demand.

---

### 3.3 Content Addressing: The "Red Blood Cell"

In the Circulatory System, filenames are irrelevant. Every piece of data is a **Blob** identified solely by its cryptographic hash. This provides two critical biological advantages:

1. **Deduplication:** If two different organs create the exact same email attachment, the Artery recognizes the identical hash and only stores/transmits one copy.
2. **Immutability:** Once a hash is generated, the data cannot change. If the data changes, the hash changes, effectively creating a new "cell" in the system.

### 3.4 The `circ` CLI Contract

Organs interact with the Circulatory System through the `circ` CLI. This keeps the organ code clean and independent of the storage backend (the "biochemistry").

| Command | Action | Biological Context |
|---------|--------|--------------------|
| **`circ push [path]`** | Hashes the file, moves it to local cache, and registers it with the Heart. | **Oxygenation:** Preparing data for circulation. |
| **`circ get [hash]`** | Checks local cache; if missing, pulls from the Heart and returns the local path. | **Absorption:** Pulling nutrients into the local tissue. |
| **`circ status`** | Returns the health of the connection to the Heart. | **Blood Pressure:** Monitoring system flow. |

---

### 3.5 The Circ Flow: From Push to Pull

The Nervous and Circulatory systems work in tandem to achieve a complete data transfer:

1. **Generation:** Organ A generates a large log file and runs `circ push log.txt`. It receives a hash: `sha256:abcd...`.
2. **Signaling:** Organ A sends a **Stimulus** via the Nervous System to Organ B containing the hash.
3. **Request:** Organ B receives the Stimulus, reads the hash, and runs `circ get sha256:abcd...`.
4. **Retrieval:** Artery B checks its local `./.circulatory/` folder. If the hash is missing, it "pulls" the chunks from the Heart.
5. **Access:** Artery B returns the local file path to Organ B for processing.

### 3.6 Ephemerality and Garbage Collection

A healthy organism does not hoard data indefinitely. The Circulatory System is designed to be **ephemeral**.

- **TTL (Time-To-Live):** Blobs in the Heart and local Arteries are often configured with a expiration timer.
- **Pruning:** If an Artery's local cache exceeds a certain storage threshold, it automatically purges the oldest or least-accessed Blobs.

This ensures that even an organism with limited disk space can process massive amounts of data over time without experiencing "organ failure" due to full storage.

---

## Chapter 4: The CLI Contract (Interfacing)

The **CLI Contract** is the definitive "truth" of the organism. It is the boundary layer that separates the internal logic of an **Organ** (the program) from the external infrastructure of the **Body** (the network and storage).

By interacting solely through standardized command-line interfaces, an organ remains "ignorant" of whether the Spinal Cord is a high-end MQTT broker or a simple local file-drop. This decoupling allows for rapid evolution, easy mocking, and extreme portability.

---

### 4.1 The Universal Handshake

Every organ, regardless of its language (Python, Node.js, Bash), uses the same three tools to interact with the world. These tools must exist in the `$PATH` of the body part.

- `stimulus`: The interface to the Nervous System.
- `circ`: The interface to the Circulatory System.
- `spark-one.sh`: The interface to the Metabolic Layer.

### 4.2 The Nervous Contract: `stimulus`

The `stimulus` CLI is used to fire nerve impulses between organs. It is asynchronous; sending a stimulus does not wait for a response, much like a neuron firing does not wait for the brain's acknowledgement.

**Command: `send`**

```bash
stimulus send --to [organ_name] --body '[json_payload]'
```

- `--to`: The unique name of the destination organ.
- `--body`: A string, typically JSON, containing the stimulus data.
- **Behavior**: The CLI returns an exit code of `0` if the message was successfully handed off to the local **Ganglion**. It does not guarantee delivery to the final destination -- that is the responsibility of the Nervous System.

**Contract Requirement (The Digest)** When an organ is sparked, it is expected to check its local `./.stimulus/` directory. The files therein are the "incoming signals."

- **Format**: JSON files.
- **Cleanup**: The organ is responsible for deleting the file after processing to prevent "re-stimulation."

---

### 4.3 The Circulatory Contract: `circ`

The `circ` CLI manages the "oxygenation" and "absorption" of data blobs. It abstracts away the complexity of SHA-256 hashing, chunking, and remote storage.

**Command: `push`**

```bash
circ push [file_path]
```

- **Output**: Returns a unique **SHA-256 hash** string to `stdout`.
- **Internal logic**: The Artery calculates the hash, copies the file to the local `./.circulatory/` cache, and ensures it is available to the **Heart** relay.

**Command: `get`**

```bash
circ get [hash]
```

- **Output**: Returns the **absolute path** to the file in the local filesystem.
- **Internal logic**: If the hash is not in the local cache, the Artery blocks until it retrieves the blob from the Heart. If retrieval fails, the CLI returns a non-zero exit code.

---

### 4.4 The Metabolic Contract: `spark-one.sh`

While `spark-cron.sh` handles the natural heartbeat, `spark-one.sh` is the interface for immediate **Excitation**.

```bash
spark-one.sh [organ_name]
```

- **Behavior**: Attempts to execute the target organ's `live.sh`.
- **Constraint**: It must still respect the `flock` concurrency gate. If the organ is already metabolizing, `spark-one.sh` does nothing. This ensures that a burst of stimuli doesn't cause a "seizure" by spawning fifty identical processes.

---

### 4.5 The "Local Lab" Mocking Strategy

The beauty of the CLI contract is that you can develop an entire organism on a single laptop without a network. In a **Local Lab** environment, you replace the complex Go or Python binaries with simple Bash mocks:

- **Mock `stimulus`**: A script that just `mkdir -p` and `cp` files into the target organ's folder.
- **Mock `circ`**: A script that just `cp` files into a central `.circulatory` folder and runs `sha256sum`.
- **Mock Heart**: There is no Heart; the Arteries just look at a shared local directory.

This allows the developer to verify the "Biology" of the organs before deploying them into the "Wild" where the Heart might be S3 and the Spinal Cord might be a global HiveMQ cluster.

---

## Chapter 5: Organ Anatomy (Implementation Guide)

In the Synthetic Organism, an **Organ** is the fundamental unit of specialized function. Physically, an organ is a self-contained directory residing within the `$ORGANS` path. It is defined not by its internal "biochemistry" (the programming language) but by its adherence to the structural and behavioral standards of the organism.

---

### 5.1 The Anatomical Structure

A healthy organ follows a predictable physical layout. This consistency allows the **Spark** to maintain it and the **Ganglion** to stimulate it without prior knowledge of its specific function.

**Standard Directory Layout:**

```text
organ_name/
 |-- live.sh          # The Entry Point (Required)
 |-- cadence           # Metabolic Rate (Optional, defaults to 1)
 |-- src/              # Internal logic (JS, Python, etc.)
 |-- .stimulus/        # Local synaptic input (Created by Ganglion)
 |-- .memory/          # Persistent local state (Organ-specific)
```

- **`live.sh`**: The only strictly required file. It must be executable.
- **`cadence`**: A plain text file containing a single integer.
- **`.stimulus/`**: A "synapse" directory. The Ganglion drops JSON files here. The organ must treat this as a volatile buffer.
- **`.memory/`**: If an organ needs to remember something between sparks (e.g., a "last\_seen\_id"), it should store it here. The organism does not guarantee the persistence of this folder across "body part" migrations, so it should be used for non-critical state.

---

### 5.2 The Entry Point (`live.sh`)

The `live.sh` file is the bridge between the **Metabolism** and the **Code**. Its primary job is to set up the environment and execute the primary logic.

**Example `live.sh` (Python):**

```bash
#!/bin/bash
# Metabolic Entry Point
# Ensure we are in the organ's directory
cd "$(dirname "$0")"

# Activate local "nutrients" (dependencies)
source ./venv/bin/activate

# Execute the logic
python3 src/main.py
```

---

### 5.3 Implementation Conventions

To ensure the organism remains maintainable by different "scientists," we follow strict coding conventions within the `src/` directory.

**JavaScript (ESM)**

We use modern ESM for JavaScript organs. To maintain the "clean" aesthetic of the organism, we follow these rules:

- **Indentation:** 2 spaces.
- **Semicolons:** None (except where technically necessary for disambiguation).
- **Modules:** Use `import`/`export` rather than `require`.

**Example `src/main.js`:**

```javascript
import fs from 'fs'
import { execSync } from 'child_process'

const digest = () => {
  const stimulusDir = './.stimulus'
  if (!fs.existsSync(stimulusDir)) return

  const signals = fs.readdirSync(stimulusDir)
  signals.forEach(file => {
    const raw = fs.readFileSync(`${stimulusDir}/${file}`)
    const stimulus = JSON.parse(raw)

    console.log('Digesting signal:', stimulus)

    // Cleanup the synapse
    fs.unlinkSync(`${stimulusDir}/${file}`)
  })
}

digest()
```

**Python**

Python organs should utilize a local `venv` within the organ directory to ensure they carry their own dependencies.

- **Indentation:** 4 spaces (Standard PEP 8).
- **Context:** Use the `circ` and `stimulus` CLI tools via `subprocess` to interact with the body.

---

### 5.4 The "Digest and Circulate" Pattern

Most organs follow a standard operational cycle once sparked:

1. **Awaken:** The `live.sh` is triggered.
2. **Digest:** The organ looks into `./.stimulus/` for new signals.
3. **Process:** Internal logic executes. If a large file is needed, it calls `circ get [hash]`.
4. **Respond:** If the organ needs to signal another part of the body, it calls `stimulus send`.
5. **Excrete:** If the organ produced a new data blob, it calls `circ push [path]` and transmits the resulting hash via `stimulus`.
6. **Sleep:** The process exits, releasing the `flock` lock.

### 5.5 Dependency Management

An organ should be "autotrophic" -- it must carry its own dependencies.

- **Node.js:** Run `npm install` within the organ folder so `node_modules` exists locally.
- **Python:** Create a `venv` and `pip install` within the organ folder.

This ensures that moving an organ to a new "Body Part" (container) is as simple as copying the directory. As long as the new host has the basic runtime (Node or Python) and the **CLI Contracts**, the organ will thrive.

---

## Chapter 6: Local Lab (Mocking and Testing)

The true strength of the CLI Contract is **Simulated Biochemistry**. You do not need a cloud-native Spinal Cord (MQTT) or a high-speed Heart (NATS) to develop and test your Organs. In the **Local Lab**, we replace these complex systems with simple Bash scripts that mimic the behavior of the global organism using nothing but the local filesystem.

---

### 6.1 The Mock Environment

To stand up a local organism, create a central directory with the following structure:

```text
organism/
 |-- bin/              # The Mock CLI Tools (stimulus, circ, spark)
 |-- organs/           # Your Organ directories (e.g., logger, processor)
 |-- .circulatory/     # The "Heart" (Local Shared Folder)
 |-- .ticks/           # Metabolic state tracking
```

Ensure your environment is aware of the anatomy:

```bash
export ORGANS="./organs/logger:./organs/processor"
export PATH="$PATH:$(pwd)/bin"
```

---

### 6.2 The Nervous Mock (`bin/stimulus`)

In the lab, there is no network. The `stimulus` mock simply performs a physical delivery to the target organ's synapse.

```bash
#!/bin/bash
# Mock Nervous System
while [[ $# -gt 0 ]]; do
  case $1 in
    --to) TARGET="$2"; shift 2 ;;
    --body) BODY="$2"; shift 2 ;;
    *) shift ;;
  esac
done

STIM_DIR="./organs/$TARGET/.stimulus"
mkdir -p "$STIM_DIR"
echo "$BODY" > "$STIM_DIR/$(date +%s)_$RANDOM.json"

# Trigger immediate excitation
spark-one.sh "$TARGET"
```

---

### 6.3 The Circulatory Mock (`bin/circ`)

The circ mock uses the local `sha256sum` utility to simulate content addressing.

```bash
#!/bin/bash
# Mock Circulatory System
CMD=$1
HEART_DIR="./.circulatory"
mkdir -p "$HEART_DIR"

if [ "$CMD" == "push" ]; then
  FILE_PATH=$2
  HASH=$(sha256sum "$FILE_PATH" | awk '{print $1}')
  cp "$FILE_PATH" "$HEART_DIR/$HASH"
  echo "$HASH"
elif [ "$CMD" == "get" ]; then
  HASH=$2
  if [ -f "$HEART_DIR/$HASH" ]; then
    echo "$(realpath $HEART_DIR/$HASH)"
  else
    exit 1
  fi
fi
```

---

### 6.4 The Metabolic Mock (`bin/spark-cron.sh`)

The mock spark follows the same logic as the production version, iterating through the `$ORGANS` variable.

```bash
#!/bin/bash
# Mock Metabolic Spark
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

### 6.5 The Excitation Mock (`bin/spark-one.sh`)

```bash
#!/bin/bash
# Adrenaline Shot
ORGAN_NAME=$1
# Find organ path from $ORGANS
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

### 6.6 Running a Lab Test

Once your mocks are in place, you can verify a full biological cycle:

1. **Excite the Organ:** Run `stimulus --to logger --body '{"test": "circ"}'`.
2. **Verify Metabolism:** Check if the `logger` organ sparked. Since `stimulus` calls `spark-one.sh`, it should run immediately regardless of cadence.
3. **Check Digestion:** The `logger` organ should have read the JSON from its `./.stimulus/` folder, performed its task, and deleted the file.
4. **Trace Circulation:** If the organ performed a `circ push`, check the `./.circulatory/` folder for the hashed file.

This local setup ensures that when you finally move your organs into a "Production Body" with HiveMQ and Synadia, the only thing that changes is the binary behind the command -- **the Organ never knows the difference.**

---

This concludes the **Synthetic Organism Biology: A Technical Field Manual**. Your organism is now ready for implementation.
