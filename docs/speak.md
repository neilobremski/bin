---
name: speak
description: Cross-platform text-to-speech. Use when the user wants to hear text spoken aloud or for audio notifications.
allowed-tools: Bash(speak *)
---

# speak

Cross-platform text-to-speech using the best available engine on the current platform.

## Usage

```bash
# Pass text as arguments
speak "Hello, world"

# Pipe text from stdin
echo "Hello, world" | speak
```

## Supported Engines (in priority order)

| Engine | Platform |
|--------|----------|
| `say` | macOS (built-in) |
| `spd-say` | Linux (Speech Dispatcher) |
| `espeak-ng` / `espeak` | Linux |
| `powershell.exe` SpeechSynthesizer | WSL |

The script auto-detects the platform and uses the first available engine.
