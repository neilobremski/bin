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
n0b ai speak brief.txt --voice af_nicole --speed 1.1

n0b ai speak --voice af_nicole --save       # sticky default voice
n0b ai speak notes.md --replace '\ba8s\b => A eight S'
n0b ai speak notes.md --pronounce 'Pay-i => pˈeɪ ˈaɪ'
n0b ai speak --replace 'Pay-i => Pay eye' --save   # add to global file
```

- **Input** is cleaned for listening by default: code fences and table
  rows are dropped, URL links unwrapped, emphasis markers stripped.
  Misaki phoneme overrides like `[word](/ipa/)` are kept. Pass `--raw` to
  synthesize the text exactly as given.
- **Output** format follows the extension: `.wav` (native, 24 kHz) or
  `.m4a`/`.aac` (converted with macOS `afconvert`). Default is
  `<input>.wav`.
- **Voices**: any Kokoro voice id (e.g. `af_bella`, `af_nicole`, `am_adam`;
  comma-separated names are averaged). Set a sticky default with
  `--voice NAME --save`; stored in `~/.config/n0b/speak-voice.txt`.
- `--speed` is a multiplier (1.0 = normal).

## Replacements

Spoken-form rewrites run **before** synthesis — the right-hand side is
what Kokoro hears:

- `~/.config/n0b/speak-replacements.txt` — one `pattern => spoken` per
  line, `#` comments allowed; left side is a Python regex.
- `--replace` — repeatable; `--save` appends to the global file (deduped
  by pattern).

Use for acronyms and jargon where IPA is overkill: `\bAPI\b => A P I`.

## Pronunciations

IPA overrides wrap matches as misaki markup `[word](/ipa/)` before
synthesis:

- `~/.config/n0b/speak-pronunciations.txt` — one `pattern => ipa` per
  line, `#` comments allowed.
- `--pronounce` — repeatable; `--save` appends to the global file.

Pronunciations run before replacements. IPA reference:
[misaki EN_PHONES.md](https://github.com/hexgrad/misaki/blob/main/EN_PHONES.md).

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
