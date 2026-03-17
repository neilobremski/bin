# Life System Textbook

| Chapter | Covers |
|---------|--------|
| [spark.md](spark.md) | Layer 0: the life spark |
| [layers.md](layers.md) | The 3-layer activation model |
| [organ-contract.md](organ-contract.md) | The organ interface |
| [stimulus.md](stimulus.md) | Per-organ stimulus (plain text lines) |
| [nervous-system.md](nervous-system.md) | MQTT signals between organs |

## Terminology

| Term | Meaning |
|------|---------|
| **Organ** | Autonomous component with an executable `live.sh` |
| **Muscle** | Executes but does not think. No autonomy. |
| **Nerve** | Communication channel (MQTT, bridges) |
| **Life Spark** | Layer 0 launcher. Sources `life.conf`, sparks organs. |
| **Spinal Cord** | Organ that bridges MQTT into per-organ `stimulus.txt`. |
| **life.conf** | Sourceable shell config. `ORGANS` plus environment. |
