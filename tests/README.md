# Life Spark Test Suite

Docker-based test suite for the life spark system. Runs in an Alpine container
to simulate clean-machine first runs.

## Build and Run

```bash
# Build the test image
docker build -t spark-tests -f tests/Dockerfile .

# Run all tests
docker run --rm spark-tests

# Run with verbose output
docker run --rm -e VERBOSE=1 spark-tests
```

## Test Groups

- **Group A (Discovery)**: organ discovery via CLI arg, env var, local/home manifest
- **Group B (Singleton)**: PID-based singleton enforcement and stale PID recovery
- **Group C (Cadence)**: cadence-based scheduling via organ.json and .spark.last
- **Group D (Execution)**: live.sh execution, permissions, missing dirs, detached processes
- **Group E (Cron/Install)**: cron installation, idempotency, log directory creation

## Output Format

TAP-like output with pass/fail counts:

```
ok 1 - A1: No organs configured -> clean exit
ok 2 - A2: CLI argument manifest
not ok 3 - B2: Singleton check
# Expected "already running" in log
```
