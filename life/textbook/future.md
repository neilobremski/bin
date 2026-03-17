# Future / Non-Goals

Ideas discussed and deferred. Tracked here so they don't get lost.

## Outbox Directory for Organ-to-Organ Messaging

Organs could write messages to `outbox/` (one file per message) instead of calling `mqtt-pub` directly. The ganglion would scan outbox directories and publish them. Benefits: organs don't need MQTT, pure filesystem. Risk: file stomping if using a single `outbox.txt` — an `outbox/` directory with atomic file-per-message is safer. Deferred until the pattern is needed beyond the ganglion and lymph node.

## Docker Integration Tests

The tadpole's distributed test (Part 3) uses separate host directories. Docker containers were attempted but the spark's background subshells exit before the container does. Solvable with `wait` or foreground execution, but deferred.

## Stimulus Overflow Protection in Spark

The spark could check stimulus.txt size before launching an organ and truncate if oversized. Currently handled by the lymph node, which is sufficient.
