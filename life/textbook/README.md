# Organism Biology Textbook

The living specification of the organism architecture.

This directory contains the tested, agreed-upon specs for how the organism works.
Each file is a concise contract -- not aspirational, but what actually ships.
Each file is a testable, incremental spec.

## Table of Contents

| File | What it covers |
|------|---------------|
| [layers.md](layers.md) | The 3-layer activation model (Spark, Spinal Cord, Organs) |
| [organ-contract.md](organ-contract.md) | The organ interface: organ.json, live.sh, health.json |
| [stimulus.md](stimulus.md) | Per-organ stimulus.jsonl format and atomicity guarantees |
| [spark.md](spark.md) | Layer 0: the life spark specification |

## Terminology

| Term | Meaning |
|------|---------|
| **Organ** | Autonomous, thinking component. Has `live.sh` (required) + `organ.json` and `health.json` (optional). |
| **Muscle** | Capability that executes but does not think. No CLAUDE.md, no autonomy. |
| **Nerve** | Communication channel (MQTT bus, GAS bridge, etc). |
| **Life Spark** | Layer 0 launcher. Intentionally dumb. Reads config, kicks organs. |
| **Soul** | `CLAUDE.md` -- identity DNA for the whole organism. |
| **Constitution** | Stable identity anchor, changed only by mutual agreement. |

## Status Values

An organ reports one of these in `health.json`:

- **ok**: Organ completed its last cycle successfully.
- **degraded**: Running but with errors or missing dependencies.
- **down**: Not running or unreachable. Spark will attempt restart on next cycle.

## How This Directory Grows

1. Spec is agreed upon.
2. Engineer agent writes it here.
3. Critic agent reviews it.
4. Tests validate it.
5. Implementation follows the spec, not the other way around.

If the implementation diverges from these specs, the spec is updated first,
then the implementation follows. The spec is the source of truth.
