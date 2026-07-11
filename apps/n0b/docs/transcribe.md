---
name: "n0b-transcribe"
description: "Transcribe an audio file to text with a local Whisper model. Use when the user provides audio (voice memo, recording) and wants its text."
allowed-tools: Bash(n0b ai transcribe *)
---

# n0b ai transcribe

Local speech-to-text via [openai-whisper](https://github.com/openai/whisper).
No API key; the model runs on this machine. Transcription goes to stdout,
everything else (setup, progress) to stderr, so output is pipe-safe.

## Usage

```bash
n0b ai transcribe memo.m4a
n0b ai transcribe memo.m4a > memo.txt

n0b ai transcribe memo.m4a --hint "Pay-i" --hint "a8s, r4t"   # bias proper nouns
n0b ai transcribe memo.m4a --language en                       # skip auto-detect
n0b ai transcribe memo.m4a --model base                        # smaller/faster model
```

Any format ffmpeg can read works: m4a, mp3, wav, aiff, ogg, mp4, ...

## Hints

Whisper mishears names and jargon; hints are fed in as its `initial_prompt`
to bias decoding. Two sources, merged (file first, then flags):

- `~/.config/n0b/transcribe-hints.txt` — one hint per line, `#` comments
  allowed. Put your recurring vocabulary here once instead of repeating
  `--hint` on every call.
- `--hint` / `--hints` — repeatable; each value may hold several
  comma-separated terms.

## Models

`--model` takes any Whisper model name: `tiny`, `base`, `small`, `medium`,
`large`, `turbo` (default). `turbo` is near-large accuracy at ~8x speed;
use `base` when speed matters more than proper nouns.

## First run

Bootstraps a dedicated venv at `~/.cache/n0b/whisper-venv` (torch is heavy —
this keeps it out of the repo venv) and downloads the model to
`~/.cache/whisper/`. One-time cost of a few GB; subsequent runs are offline.
Requires `ffmpeg` on PATH.
