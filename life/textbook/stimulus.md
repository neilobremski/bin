# Stimulus

Stimulus is how signals reach organs. Each organ can have a `stimulus.txt` in its directory. The nervous system (or anything else) appends lines. The organ reads, processes, and empties the file.

## Writing

Append a line:

```bash
echo "wake" >> organs/brain/stimulus.txt
echo "email from neil circ:abc123" >> organs/brain/stimulus.txt
```

Short writes (under 4096 bytes) with `>>` are effectively atomic on Linux. Multiple writers can safely append without locking.

## Reading

The organ reads and empties the file when it runs:

```bash
lines=$(cat stimulus.txt)
> stimulus.txt
# process $lines
```

For concurrent-safe consumption, use flock:

```bash
exec 9>stimulus.txt.lock
flock 9
lines=$(cat stimulus.txt)
> stimulus.txt
exec 9>&-
```

## What Goes in a Line

Whatever the organ needs. The format is up to the writer and the organ. Examples:

```
wake
email from neil subject:Deploy check circ:a1b2c3d4
sms from +1234567890 circ:e5f6a7b8
health-alert phone offline 5 min
check calendar
```

For payloads too large for a text line (email bodies, attachments, voice memos), the line carries a reference to the circulatory system:

```
email from neil subject:Tiger Claw circ:circ/2026-03-17/a1b2c3d4
voicemail circ:circ/2026-03-17/redmond-middle-school-14
```

The organ retrieves the payload from the circulatory system using the reference. The stimulus line is just the signal — small, fast, appendable.

## Dedup

Up to the organ. A simple approach: track seen lines (or hashes of lines) and skip repeats. The stimulus format does not enforce dedup — organs that need it implement it.

## Priority

Up to the organ. A convention: lines starting with `URGENT` get processed first. But this is organ behavior, not a wire protocol rule.
