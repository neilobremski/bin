# Ear

Audio transcription organ. Auto-detects the best available Whisper provider.

## Quick Start

From this directory:
```bash
# Detect which provider is available
bin/transcribe --detect

# Transcribe an audio file
bin/transcribe recording.mp3

# With options
bin/transcribe recording.mp3 --language en --prompt "proper nouns here"

# Force a specific provider
bin/transcribe recording.mp3 --provider groq

# Full JSON output (includes provider used)
bin/transcribe --json recording.mp3
```

Output is plain text by default, or JSON with `--json`.

## Providers

Checked in order. First available wins, cached in `.memory/whisper-provider`.

| Provider | Requires | Notes |
|----------|----------|-------|
| `whisper.cpp` | Binary on PATH | Local, no network, no API key |
| `groq` | `GROQ_API_KEY` | Free tier: 2,000 req/day |
| `openai` | `OPENAI_API_KEY` | Supports `OPENAI_BASE_URL` for proxies |

Cache is re-validated on each use; if the cached provider is unavailable, a new one is detected.

## Organ Contract

```
ear/
├── live              # Entry point (called by spark)
├── cooldown          # 1 = fire every other tick
├── bin/transcribe    # Synchronous CLI for testing
├── src/              # Python modules
├── tests/            # pytest suite
├── .stimulus/        # Incoming signals
└── .memory/          # Provider cache
```

## Stimulus Protocol

```bash
stimulus send --to ear --body '{
  "action": "transcribe",
  "audio_path": "/path/to/recording.mp3",
  "language": "en",
  "prompt": "proper nouns here",
  "provider": "groq",
  "id": "corr-001",
  "from": "brain"
}'
```

Also accepts `audio_hash` for circ-pushed audio.

Response:
```json
{"id": "corr-001", "action": "transcribe", "status": "ok", "text": "...", "provider": "groq"}
```

## Limits

- Max file size: 25 MB
- Supported formats: FLAC, MP3, M4A, OGG, WAV, WEBM

## Tests

```bash
python3 -m pytest tests/ -v
```
