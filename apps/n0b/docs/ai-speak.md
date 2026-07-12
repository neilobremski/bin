---
name: "n0b-speak"
description: "Synthesize text or markdown into spoken audio with a local Kokoro model. Use when the user wants a document read aloud or an audio file of some text."
allowed-tools: Bash(n0b ai speak *)
---

# n0b ai speak

Local text-to-speech: reads a text or markdown file (or stdin) aloud into
an audio file with the Kokoro-82M model. Fully offline after first-run
setup; no API keys.

```bash
n0b ai speak notes.md -o notes.m4a          # markdown -> spoken m4a
echo "ship it" | n0b ai speak - -o say.wav  # stdin -> wav
n0b ai speak brief.txt --voice am_adam --speed 1.1
```

- **Input** is cleaned for listening by default: code fences and table
  rows are dropped, links unwrapped, emphasis markers stripped. Pass
  `--raw` to synthesize the text exactly as given.
- **Output** format follows the extension: `.wav` (native, 24 kHz) or
  `.m4a`/`.aac` (converted with macOS `afconvert`). Default is
  `<input>.wav`.
- **Voices**: any Kokoro voice id (default `af_heart`; e.g. `am_adam`,
  `bf_emma` — the leading letter picks the language/accent pipeline).
- `--speed` is a multiplier (1.0 = normal).

## Setup (automatic, one-time)

First run creates `~/.cache/n0b/kokoro-venv` (needs a Python with spacy
wheels — 3.13/3.12 preferred over newer), installs `kokoro`, `soundfile`,
and `phonemizer`, and downloads the model from Hugging Face (~330 MB).
Names and other out-of-dictionary words also need the espeak-ng library:
`brew install espeak-ng` (one manual step — without it, synthesis fails
on unusual words).

Pairs with [n0b ai transcribe](transcribe.md) (speech-to-text) for a full
local audio round trip, and with `tell <agent> --attach out.m4a` to ship
the audio anywhere on the a8s network.
