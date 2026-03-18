# Tadpole

Life is brutally simple.

A tadpole is the simplest organism that proves the life system works. It has six organs, one nervous system, and a short, eventful life.

## The Lifetime Story

A tadpole's life goes like this:

1. **It has a heartbeat.** The heart beats on a cadence. If you spark it again too soon, the cadence blocks the second beat. This proves the spark respects timing.

2. **It can receive signals.** Someone sends "swim now" to the tail via the nervous system. The ganglion picks up the message and delivers it. The tail wakes up and swims. This proves dormant organs wake on stimulus.

3. **It can eat and digest.** The stomach gets food, digests it into a payload (stored in the circulatory system), and sends it to the tail via the `stimulus` CLI. The tail retrieves the payload and swims with it. This proves organs can produce data, store it, and another organ can retrieve it — without either organ knowing about the other.

4. **It can get sick and recover.** The lymph node scans every organ's health. When something reports an error, the lymph node flags the organism as degraded. When stimulus queues overflow, the lymph node truncates them. This proves the immune system detects problems and cleans up.

5. **It can be fed by a human.** The eye organ watches a Google Sheet. When you type a command ("eat", "swim now"), the eye delivers it as stimulus to the right organ. Health status is written back to the sheet so you can watch the tadpole respond in real time. This proves the organism can interact with the outside world.

`lifetime.sh` tests chapters 1-4 automatically. Chapter 5 is interactive — run the tadpole continuously and feed it through the spreadsheet.

## Organs

| Organ | Role | Cadence |
|-------|------|---------|
| heart | Beats, writes health | 1 min |
| ganglion | Scans health, maintains registry, routes signals | 1 min |
| eye | Reads commands from Google Sheet, writes health back | 1 min |
| tail | Swims when told to (dormant until stimulus) | none |
| stomach | Digests food into circulatory payload (dormant until stimulus) | none |
| lymph | Scans health, cleans overflows | 1 min |

## Running

```bash
# Automated tests (chapters 1-4)
cd tadpole
./lifetime.sh

# Interactive mode (chapter 5) — feed it through the spreadsheet
SPARK_INTERVAL=10 ../life/spark-loop.sh
```

## The Spreadsheet

The tadpole's Google Sheet has two areas:

**Input (columns A-D)**: Write commands here. The eye reads them each cycle.

| Command | Target | Processed | Response |
|---------|--------|-----------|----------|
| eat | stomach | yes | ok yum yum (meal 1) |
| swim now | tail | yes | ok splish splash |
| eat | stomach | | |

**Status (columns F-I)**: The eye writes organ health here each cycle.

| Organ | Status | Health | Updated |
|-------|--------|--------|---------|
| heart | ok | ok beat 42 | 09:31 |
| stomach | ok | ok yum yum (meal 3) | 09:30 |
| tail | ok | ok splish splash | 09:31 |
| lymph | ok | ok all healthy | 09:31 |

## Configuration

In `life.conf`:
```bash
SHEETS_NAME=Tadpole    # eye finds or creates a sheet with this name
```

The eye automatically finds an existing Google Sheet by name, or creates one if none exists. Each person running the tadpole with their own GAS bridge gets their own sheet.

Requires the GAS bridge (`GAS_BRIDGE_URL` and `GAS_BRIDGE_KEY` via `local-secret`). Without it, the eye stays idle — degradation, not failure.
