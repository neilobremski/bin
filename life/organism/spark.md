# Life Spark -- Layer 0 Specification

The spark is the heartbeat of the organism. It is intentionally dumb.

**Location**: `life/spark.sh` (lives in `~/bin` on every machine)
**Trigger**: Cron every minute: `* * * * * ~/bin/life/spark.sh`
**Config**: Organ directories discovered in priority order (first match wins)

## Properties

- **No intelligence.** No LLM, no API calls, no network access.
- **No persistence.** Runs, checks, launches, exits. Cron calls it again.
- **Pure filesystem.** Reads config files, checks PIDs, writes timestamps.
- **Idempotent.** Running it twice in a row produces the same result.
- **Fast.** Must complete in under 1 second.

## Organ Discovery

The spark finds organ directories using the first source that matches:

| Priority | Source | Example |
|----------|--------|---------|
| 1 | **Argument** — a manifest file path passed on the command line | `spark.sh /path/to/manifest.conf` |
| 2 | **`$ORGANS` env** — colon-separated directory paths (like `PATH`) | `ORGANS=/opt/brain:/opt/eyes spark.sh` |
| 3 | **Local manifest** — `organs.conf` next to the script | `life/organs.conf` |
| 4 | **Home manifest** — `~/organs.conf` | `~/organs.conf` |

A manifest file lists one organ directory path per line. Blank lines and
`#` comments are ignored. Relative paths resolve from the manifest's directory.

```conf
# organs.conf -- organs managed on this machine
/home/user/organs/brain
# /home/user/organs/circadian  # uncomment when ready
```

Each path must contain an executable `live.sh`. The `organ.json` manifest
is optional — if missing, the organ runs every spark cycle (no cadence gating).

## Algorithm

```
for each discovered organ directory:
    path = organ directory

    1. VALIDATE: does path/live.sh exist and is it executable?
       no  -> warn, skip

    2. SINGLETON CHECK: is the organ already running?
       - read PID from path/.spark.pid
       - check with kill -0 <pid>
       running -> skip (already active)

       NOTE: The spark does NOT check dependencies. Dependency checking
       is the organ's responsibility at startup.

    3. CADENCE CHECK (only if path/organ.json exists):
       - parse organ.json for cadence field
       - read epoch from path/.spark.last
       - if (now - last) < cadence minutes -> skip
       - if organ.json is missing or has no cadence -> run every cycle

    4. LAUNCH:
       - nohup path/live.sh >> path/.spark.log 2>&1 &
       - write PID to path/.spark.pid
       - write epoch to path/.spark.last
       - log: "started <organ_name> (PID $!)"
```

## Cadence Values

| Value | Meaning |
|-------|---------|
| Integer (e.g., `5`) | Spark every N minutes |
| `"manual"` | Never auto-sparked. Only launched by explicit command. |
| `"on-stimulus-or-cooldown"` | Spark when `stimulus.jsonl` exists and has size > 0 bytes (file size check only, no JSONL parsing), otherwise respect a cooldown period (`cooldown_minutes`, falling back to `health_ttl_minutes`). |

For `"on-stimulus-or-cooldown"`: the spark checks if `stimulus.jsonl` exists
and has size > 0 bytes (the spark does not parse the JSONL content -- it just
checks file size). If yes, spark the organ (respecting singleton). If no,
fall back to `cooldown_minutes` from organ.json as the cooldown period. If
`cooldown_minutes` is not specified, `health_ttl_minutes` is used as the fallback.

## Sub-Minute Frequency

For sub-minute frequency, use a systemd timer instead of cron.
For most organs, once per minute is sufficient. The spinal cord + immediate
stimulus pattern handles real-time needs without sub-minute spark frequency.

## Logging

The spark logs to stderr (captured by cron or systemd). Each decision is
logged at a single line:

```
2026-03-16T14:30:01 SPARK brain: sparked (pid 12345)
2026-03-16T14:30:01 SPARK circadian: skipped (outside activity window)
2026-03-16T14:30:01 SPARK spinal-cord: skipped (already running, pid 12340)
```

## Failure Modes

| Failure | Result |
|---------|--------|
| No organs configured | Spark exits cleanly (code 0), logs message |
| live.sh missing or not executable | Skip that organ, log warning |
| organ.json missing | Organ runs every cycle (no cadence gating) |
| organ.json invalid JSON | Skip cadence check, treat as missing |
| live.sh fails (non-zero exit) | Organ reports health, spark retries next cycle |
| Spark itself crashes | Cron restarts it next minute |

The spark never crashes the organism. Every error is logged and skipped.
The next cron cycle gets another chance.
