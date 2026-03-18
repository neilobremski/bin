# Circulatory System

The circulatory system carries payloads between organs. Stimulus lines carry signals (small, fast); the circulatory system carries data (files, images, audio, documents).

## How It Works

An organ stores a payload and gets back a content-addressed reference (first 16 hex characters of the SHA-256 hash — 64 bits). The reference travels through stimulus. The receiving organ retrieves the payload using the reference.

```bash
# Organ A: store a payload, signal through the nervous system
ref=$(echo "meal digested" | circ-put -)
stimulus send tail "food circ:$ref"

# Organ B: retrieve the payload
ref=$(grep -o 'circ:[^ ]*' stimulus.txt | cut -d: -f2)
payload=$(circ-get "$ref")
```

## Content Addressing

Same content = same hash = no duplicate storage. Upload the same file twice, get the same reference, one copy stored. This prevents the cancerous growth of duplicated data.

## Backends

Local storage is always used (fast, works offline). If the GAS bridge is configured and `CIRC_LOCAL_ONLY` is unset, payloads are also uploaded to Google Drive for cross-body-part access.

| Backend | Storage | When |
|---------|---------|------|
| local | `~/.life/circ/` (or `$CIRC_DIR`) | Always |
| gdrive | Google Drive via `gas` CLI | When GAS bridge is available |

Organs don't know which backend is active. They call `circ-put` and `circ-get` — the backend is configuration, not code.

## Stimulus + Circulatory = Complete Signal

A stimulus line can carry both signal and data reference:

```
food circ:a1b2c3d4e5f6a7b8
voicemail circ:9f8e7d6c5b4a3210
email from neil subject:Deploy circ:fc87f26abfde0f84
```

The signal is immediate (text line). The payload is deferred (retrieved on demand). This keeps stimulus.txt small and fast while supporting arbitrarily large payloads.

## Cleanup

The lymph node (immune system) can scan `~/.life/circ/` for old files and delete them. Content-addressed files with no recent references are safe to prune. This prevents the circulatory system from filling up disk — the same principle as stimulus overflow truncation.
