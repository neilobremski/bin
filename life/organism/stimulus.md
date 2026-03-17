# Stimulus Specification

Stimulus is how signals reach organs. Each organ has its own `stimulus.jsonl`
file in its organ directory (`organs/<name>/stimulus.jsonl`).

This is a per-organ file -- NOT the global `stimulus-queue.jsonl` from the
earlier architecture. The new design routes stimulus to individual organs
via the spinal cord (Layer 1).

## Format

JSONL: one JSON object per line, newline-terminated.

### Minimum Fields

Every stimulus line MUST have:

| Field | Type | Description |
|-------|------|-------------|
| type | string | What kind of stimulus (e.g., `"email"`, `"chat"`, `"health_alert"`, `"cron"`) |
| ts | string | ISO 8601 UTC timestamp (e.g., `"2026-03-16T21:30:00Z"`) |
| body | string | The stimulus content |

### Optional Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| priority | string | `"batch"` | `"immediate"` or `"batch"` |
| content_hash | string | -- | 16-char SHA-256 hex prefix for dedup |
| sender | string | -- | Who/what sent this stimulus |
| subject | string | -- | Subject line (for emails, alerts) |
| snippet | string | -- | Short preview (for UI display) |

### Examples

```jsonl
{"type":"email","ts":"2026-03-16T21:30:00Z","body":"Can you check the deploy?","sender":"ops","subject":"Deploy check","priority":"immediate","content_hash":"a1b2c3d4e5f6a7b8"}
{"type":"health_alert","ts":"2026-03-16T21:31:00Z","body":"Phone tentacle offline for 5 minutes","sender":"brainstem","priority":"batch"}
{"type":"chat","ts":"2026-03-16T21:35:00Z","body":"What's your status?","sender":"neil","content_hash":"f8e7d6c5b4a39281"}
```

## Timezone

Stimulus timestamps are always **UTC** (with trailing `Z`).
This differs from health.json which uses naive local time.
When in doubt, use UTC for stimulus.

## Atomicity

### Writing Stimulus

On Linux, short O_APPEND writes to regular files are effectively atomic
due to kernel inode locking. Keep each stimulus line well under 4096 bytes.
This is a Linux implementation behavior, not a POSIX guarantee.

This means multiple writers (spinal cord, brainstem, direct injection) can
safely append to the same file without explicit locking, as long as each
write is a single echo/write call well under 4096 bytes.

### Reading and Consuming Stimulus

The organ must read and remove processed lines. Recommended pattern:

```bash
exec 9>stimulus.jsonl.lock
flock 9
lines=$(cat stimulus.jsonl)
> stimulus.jsonl
exec 9>&-
# process $lines
```

Alternatively, `mv stimulus.jsonl stimulus.processing` works but may lose
lines written between the rename and the next append.

## Deduplication

Content hashing is recommended but not required.

When used, `content_hash` is a 16-character hex prefix of the SHA-256 hash
of the stimulus body. Organs that track seen hashes can skip duplicates.

```python
import hashlib
content_hash = hashlib.sha256(body.encode()).hexdigest()[:16]
```

The existing idempotent stimulus infrastructure in the codebase already
uses this pattern. New organs should follow it for consistency.

## Priority

- `"immediate"`: Organ should process this stimulus as soon as possible.
  The spinal cord kicks the spark after writing immediate stimulus,
  providing near-real-time activation.
- `"batch"`: Organ processes this on its next regular cycle. No extra
  spark kick needed.

Default is `"batch"` if omitted.
