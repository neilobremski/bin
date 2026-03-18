# Life System Textbook

Life is brutally simple. Four text files define the entire contract:

- **life.conf** — what to run, what environment to give it
- **organ.conf** — per-organ config (cadence, etc.)
- **stimulus.txt** — a signal arrived
- **health.txt** — how the organ is doing

Everything is shell. No JSON. No parsing. Just `source` and `echo`.

## Chapters

| Chapter | Covers |
|---------|--------|
| [spark.md](spark.md) | Layer 0: the life spark |
| [layers.md](layers.md) | The 3-layer activation model |
| [organ-contract.md](organ-contract.md) | The organ interface |
| [stimulus.md](stimulus.md) | Per-organ stimulus (plain text lines) |
| [nervous-system.md](nervous-system.md) | MQTT signals between organs |
| [immune-system.md](immune-system.md) | Health monitoring and cleanup |
| [circulatory-system.md](circulatory-system.md) | Payload transfer between organs |
| [future.md](future.md) | Deferred ideas and non-goals |

## Terminology

| Term | Meaning |
|------|---------|
| **Organ** | Autonomous component with an executable `live.sh` |
| **Muscle** | Executes but does not think. No autonomy. |
| **Ganglion** | Per-body-part nervous system node. Maintains organ registry (SQLite), routes stimulus, broadcasts health. |
| **Lymph Node** | Scans organ health, cleans overflows, emits summary |
| **stimulus** | CLI tool for organs to send signals and query health through the nervous system |
| **life.conf** | Sourceable shell config. `ORGANS` plus environment. |
| **organ.conf** | Sourceable shell config per organ. `CADENCE` plus organ-specific vars. |
