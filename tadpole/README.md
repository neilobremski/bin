# Tadpole

Life is brutally simple.

A tadpole is the simplest organism that proves the life system works. It has seven organs, one nervous system, and a short, eventful life.

## The Lifetime Story

A tadpole's life goes like this:

1. **It has a heartbeat.** The heart beats on a cadence. If you spark it again too soon, the cadence blocks the second beat. This proves the spark respects timing.

2. **It can receive signals.** Someone sends "swim now" to the tail via the nervous system. The ganglion picks up the message and delivers it. The tail wakes up and swims. This proves dormant organs wake on stimulus.

3. **It can eat and digest.** The stomach gets food, digests it into a payload (stored in the circulatory system), and sends it to the tail via the `stimulus` CLI. The tail retrieves the payload and swims with it. This proves organs can produce data, store it, and another organ can retrieve it — without either organ knowing about the other.

4. **It can get sick and recover.** The lymph node scans every organ's health. When something reports an error, the lymph node flags the organism as degraded. When stimulus queues overflow, the lymph node truncates them. This proves the immune system detects problems and cleans up.

5. **It can be fed by a human.** The eye organ watches a Google Sheet. When you type a command ("eat", "swim now"), the eye delivers it as stimulus to the right organ. Health status is written back to the sheet so you can watch the tadpole respond in real time. This proves the organism can interact with the outside world.

6. **It remembers.** The hippocampus stores memories in SQLite, deduplicates them, and consolidates over time. Other organs send memories via stimulus (`remember: ate a big meal`). The brain (future) queries memory directly. This proves an organism can accumulate experience across spark cycles.

`lifetime.sh` tests chapters 1-4 automatically. Chapters 5-6 are interactive.

## Organs

| Organ | Role | Cadence |
|-------|------|---------|
| heart | Beats, writes health | 1 min |
| ganglion | Scans health, maintains registry, routes signals | 1 min |
| hippocampus | Stores and consolidates memories (SQLite + FTS5) | 1 min |
| eye | Reads commands from Google Sheet, writes health back | 1 min |
| tail | Swims when told to (dormant until stimulus) | none |
| stomach | Digests food into circulatory payload (dormant until stimulus) | none |
| lymph | Scans health, cleans overflows | 1 min |

## Memory

The hippocampus owns `memory.db` — a SQLite database with FTS5 full-text search. Other organs interact through stimulus or the `memories` CLI:

```bash
# Store a memory (via nervous system)
stimulus send hippocampus "remember: the stomach ate meal 3"
stimulus send hippocampus "remember important: learned to swim faster"
stimulus send hippocampus "remember critical: human fed me for the first time"

# Store directly (same body part, fast path)
memories store "the tail went splish splash"
memories store -i 8 "this food was especially good"
memories store -c food "ate algae at 09:30"

# Search memories
memories search "food"
memories recent 5
memories stats
```

The brain (future organ) reads `memory.db` directly — no network round-trip, no stimulus delay. This is high-bandwidth local access, same as the ganglion reading `health.txt` files.

## Running

```bash
# Automated tests (chapters 1-4)
cd tadpole
./lifetime.sh

# Interactive mode (chapters 5-6) — feed it through the spreadsheet
SPARK_INTERVAL=10 ../life/spark-loop.sh
```

## The Spreadsheet

The eye reads commands from a Google Sheet named "Tadpole" (created automatically).

| Command | Target | Processed | Response |
|---------|--------|-----------|----------|
| eat | stomach | yes | ok yum yum (meal 1) |
| swim now | tail | yes | ok splish splash |
| remember: I like algae | hippocampus | yes | ok 1 memories (stored 1) |

## Configuration

In `life.conf`:
```bash
SHEETS_NAME=Tadpole    # eye finds or creates a sheet with this name
```

The eye automatically finds an existing Google Sheet by name, or creates one if none exists. Each person running the tadpole with their own GAS bridge gets their own sheet.

Requires the GAS bridge (`GAS_BRIDGE_URL` and `GAS_BRIDGE_KEY` via `local-secret`). Without it, the eye stays idle — degradation, not failure.
