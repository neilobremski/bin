# Ganglion

The nervous system's local node. One per body part.

The ganglion knows what organs exist, how they're doing, and how to reach them. Organs never talk to each other directly — they go through the ganglion via the `stimulus` CLI.

## What It Does

Each cycle (typically 1 minute):

1. **Scan** — read local organs' `health.txt`, update the registry
2. **Broadcast** — tell other ganglions what organs live here and how they're doing
3. **Receive** — merge other ganglions' broadcasts into the registry
4. **Deliver** — route any incoming stimuli to local organs' `stimulus.txt`

## The Registry

SQLite database tracking every known organ:

```
type    id          body_part   health_status   health_text         last_seen
heart   heart-aws   aws         ok              ok beat 42          2026-03-18 09:30
heart   heart-hp    hp          ok              ok beat 17          2026-03-18 09:29
tail    tail-aws    aws         ok              ok idle             2026-03-18 09:30
```

The registry is eventually consistent — as fresh as the last ganglion cycle. This is intentional. Health doesn't need to be real-time.

## Installation

The ganglion is infrastructure, like the spark. Add it to any body part's `life.conf`:

```bash
ORGANS=path/to/ganglion:organs/heart:organs/tail
```

## See Also

- [Nervous System](../life/textbook/nervous-system.md) — full design
- [Stimulus CLI](../stimulus) — how organs talk to the nervous system
