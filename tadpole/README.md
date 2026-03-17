# Tadpole

The simplest possible organism. A living test for the life system.

## Structure

- `life.conf` — organism config (`ORGANS=organs/heart`)
- `organs/heart/` — beats, writes `health.txt`
- `lifetime.sh` — 5 integration tests

## Usage

```bash
./lifetime.sh              # uses ../life/spark.sh
./lifetime.sh /path/to/spark.sh   # custom spark
```
