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

n0b ai transcribe --hint "Pay-i, k7e" --save                   # add to the global hints file
n0b ai transcribe memo.m4a --hint "Pay-i" --save               # save, then transcribe

n0b ai transcribe memo.m4a --replace 'Jerry => Gerry'          # annotate known mis-hearings
n0b ai transcribe --replace 'Jerry => Gerry' --save            # add to the global replacements file
```

stderr reports the hints in effect (and where each came from), model
loading, and a progress bar over the audio, so a long silence is never
ambiguous.

Any format ffmpeg can read works: m4a, mp3, wav, aiff, ogg, mp4, ...

## Hints

Whisper mishears names and jargon; hints are fed in as its `initial_prompt`
to bias decoding. Two sources, merged (file first, then flags):

- `~/.config/n0b/transcribe-hints.txt` — one hint per line, `#` comments
  allowed. Put your recurring vocabulary here once instead of repeating
  `--hint` on every call.
- `--hint` / `--hints` — repeatable; each value may hold several
  comma-separated terms.

`--save` appends the given `--hint` values to the global file (deduped,
case-insensitive) instead of you editing it by hand; with no audio argument
it saves and exits.

## Replacements

Hints only bias decoding; some words lose anyway ("Gerry" always comes out
"Jerry"). Replacements run **after** transcription: each is a regex plus the
likely correction, and every match gets annotated in place —

> Jerry (possible transcribe error, might be 'Gerry') said blah

— verbose by design, so downstream agents aren't misled by a confident wrong
word. Two sources, merged:

- `~/.config/n0b/transcribe-replacements.txt` — one `wrong => right` per
  line, `#` comments allowed; the left side is a Python regex.
- `--replace 'wrong => right'` — repeatable; `--save` appends these to the
  global file (deduped by pattern).

stderr reports how many patterns loaded and which ones matched.

## Models

`--model` takes any Whisper model name: `tiny`, `base`, `small`, `medium`,
`large`, `turbo` (default). `turbo` is near-large accuracy at ~8x speed;
use `base` when speed matters more than proper nouns.

## First run

Bootstraps `<bin>/.venv` on first use from `requirements/ai*.txt` (shared repo
venv; torch installs once) and downloads the model to `~/.cache/whisper/`.
One-time cost of a few GB; subsequent runs are offline. Requires `ffmpeg` on PATH.
