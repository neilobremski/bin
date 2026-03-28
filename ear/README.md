# Ear

Audio transcription organ using Groq Whisper API.

## Quick Start

From this directory:
```bash
# Set your Groq API key
export GROQ_API_KEY="your-key-here"

# Transcribe an audio file
bin/transcribe recording.mp3

# With options
bin/transcribe recording.mp3 --language en --prompt "Knobert, Neil"

# Full JSON output
bin/transcribe --json recording.mp3
```

Output is plain text by default, or JSON with `--json`.

## Organ Contract

```
ear/
├── live              # Entry point (called by spark)
├── cooldown          # 1 = fire every other tick
├── bin/transcribe    # Synchronous CLI for testing
├── src/              # Python modules
├── tests/            # pytest suite
└── .stimulus/        # Incoming signals
```

## Environment

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | Yes | Groq API key for Whisper access |

## Stimulus Protocol

```bash
# Transcribe from file path
stimulus send --to ear --body '{
  "action": "transcribe",
  "audio_path": "/path/to/recording.mp3",
  "id": "corr-001",
  "from": "brain"
}'

# Transcribe from circ hash (audio pushed via circ)
stimulus send --to ear --body '{
  "action": "transcribe",
  "audio_hash": "abc123def456",
  "language": "en",
  "prompt": "Knobert, Neil",
  "id": "corr-002",
  "from": "brain"
}'
```

Response:
```json
{"id": "corr-001", "action": "transcribe", "status": "ok", "text": "transcribed text..."}
```

## Limits

- Max file size: 25 MB
- Supported formats: FLAC, MP3, M4A, MPEG, MPGA, OGG, WAV, WEBM
- Groq free tier: 2,000 requests/day
