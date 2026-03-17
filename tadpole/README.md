# Tadpole

The simplest possible organism. A test harness for the life system (spark).

## Structure

- `organs.conf` — lists organs to spark (one per line)
- `organs/heart/` — a single organ that increments a beat counter
- `lifetime.sh` — integration test that runs the tadpole through its lifecycle

## Usage

```bash
./lifetime.sh [path/to/spark.sh]
```

Defaults to `../life/spark.sh`. Tests that spark finds organs, respects cadence,
and the heart beats on schedule.
