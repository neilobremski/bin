# Tadpole

Life is brutally simple.

A tadpole is the simplest organism that proves the life system works. It has five organs, one nervous system, and a short, eventful life.

## The Lifetime Story

A tadpole's life goes like this:

1. **It has a heartbeat.** The heart beats on a cadence. If you spark it again too soon, the cadence blocks the second beat. This proves the spark respects timing.

2. **It can receive signals.** Someone sends "swim now" to the tail via the nervous system. The ganglion picks up the message and delivers it. The tail wakes up and swims. This proves dormant organs wake on stimulus.

3. **It can eat and digest.** The stomach gets food, digests it into a payload (stored in the circulatory system), and announces "I have food" on the nervous system. The ganglion routes that announcement to the tail. The tail retrieves the payload and swims with it. This proves organs can produce data, store it, and another organ can retrieve it — without either organ knowing about the other.

4. **It can get sick and recover.** The lymph node scans every organ's health. When something reports an error, the lymph node flags the organism as degraded. When stimulus queues overflow, the lymph node truncates them. This proves the immune system detects problems and cleans up.

That's the whole life. `lifetime.sh` tests exactly these four chapters.

## Organs

| Organ | Role | Cadence |
|-------|------|---------|
| heart | Beats, publishes heartbeat | 1 min |
| ganglion | Routes nervous system messages to organs | 1 min |
| tail | Swims when told to (dormant until stimulus) | none |
| stomach | Digests food into circulatory payload (dormant until stimulus) | none |
| lymph | Scans health, cleans overflows | 1 min |

## Running

```bash
cd tadpole
../life/spark.sh          # one spark cycle
./lifetime.sh             # full lifetime test (requires mosquitto)
```
