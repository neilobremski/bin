# Circulatory System

The circulatory system carries payloads between organs. Stimulus lines carry signals (small, fast); the circulatory system carries data (files, images, audio, documents).

## How It Works

An organ stores a payload and gets back a content-addressed reference (SHA-256 hash). The reference travels through stimulus. The receiving organ retrieves the payload using the reference.

```bash
# Organ A: store a payload
ref=$(echo "meal digested" | circ-put -)
echo "food ref:$ref" >> organs/tail/stimulus.txt

# Organ B: retrieve the payload
ref=$(grep -o 'ref:[^ ]*' stimulus.txt | cut -d: -f2)
payload=$(circ-get "$ref")
```

## Content Addressing

Same content = same hash = no duplicate storage. Upload the same file twice, get the same reference, one copy stored. This prevents the cancerous growth of duplicated data.

## Backends

The `circ-put` and `circ-get` scripts read `CIRC_BACKEND` from environment:

| Backend | Storage | Use case |
|---------|---------|----------|
| `local` | `~/.life/circ/` (or `$CIRC_DIR`) | Testing, single machine |
| `drive` | Google Drive via GAS bridge | Production, distributed |

Organs don't know which backend is active. They call `circ-put` and `circ-get` — the backend is configuration, not code.

## Stimulus + Circulatory = Complete Signal

A stimulus line can carry both signal and data reference:

```
food ref:a1b2c3d4e5f6a7b8
voicemail ref:9f8e7d6c5b4a3210
email from neil subject:Deploy ref:fc87f26abfde0f84
```

The signal is immediate (text line). The payload is deferred (retrieved on demand). This keeps stimulus.txt small and fast while supporting arbitrarily large payloads.

## Cleanup

The lymph node (immune system) can scan `~/.life/circ/` for old files and delete them. Content-addressed files with no recent references are safe to prune. This prevents the circulatory system from filling up disk — the same principle as stimulus overflow truncation.
