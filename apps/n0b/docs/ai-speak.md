---
name: "n0b-speak"
description: "Read text aloud or save speech to a file. Use when the user wants to hear text spoken or create an audio attachment."
allowed-tools: Bash(n0b ai speak *)
---

# n0b ai speak

Text-to-speech for inline text, files, or stdin.

On macOS the default engine is the built-in `say(1)` voice — instant, no
model download. Use `--engine kokoro` for fully offline neural synthesis
when you need a file on Linux or want Kokoro voices.

```bash
n0b ai speak "hello"                      # play on speakers
echo "ship it" | n0b ai speak              # pipe stdin
n0b ai speak notes.md -o notes.m4a        # save file (no playback)
n0b ai speak -v Samantha "hi" --save      # sticky macOS voice

n0b ai speak notes.md --engine kokoro -o out.wav   # offline neural
n0b ai speak --replace '\ba8s\b => A eight S' --save
n0b ai speak --pronounce 'Pay-i => pˈeɪ ˈaɪ' --save   # kokoro only
```

## Output modes

| Invocation | Behavior |
|------------|----------|
| No `-o` | Play on speakers |
| `-o path` | Write audio file only (stdout prints the path) |

**say** (macOS): writes `.m4a`, `.aiff`, `.aifc` natively; `.wav` via
`afconvert`. **kokoro**: `.wav` native; `.m4a` via `afconvert`.

## Engines

- `auto` (default) — `say` when available, else `kokoro`
- `say` — macOS system voices (`say -v '?'` to list)
- `kokoro` — local Kokoro-82M (~330 MB first-run download)

## Input

Cleaned for listening by default: code fences and table rows dropped, URL
links unwrapped, emphasis stripped. Misaki `[word](/ipa/)` overrides are
kept for kokoro and flattened to the word for say. Pass `--raw` to skip
cleanup.

Inline text, a file path, `-`, or omitted (stdin) all work:

```bash
n0b ai speak hello world          # inline (no file named hello)
n0b ai speak brief.md             # file
```

## Voices

`--voice` / `-v` sets the voice for the active engine. Persist a default
with `--voice NAME --save` → `~/.config/n0b/speak-voice.txt`.

- **say**: macOS name, e.g. `Samantha`, `Daniel`
- **kokoro**: id, e.g. `af_nicole`, `af_bella` (comma-separated blends)

Kokoro-style ids are ignored when the say engine is active.

## Replacements

Spoken-form rewrites before synthesis:

- `~/.config/n0b/speak-replacements.txt` — `pattern => spoken` per line
- `--replace` — repeatable; `--save` appends (deduped by pattern)

## Pronunciations (kokoro only)

IPA overrides as misaki `[word](/ipa/)` markup:

- `~/.config/n0b/speak-pronunciations.txt`
- `--pronounce` — repeatable; `--save` appends

## Kokoro setup (one-time)

First kokoro run installs from `requirements/ai.txt` into `<bin>/.venv` (shared
repo venv) and downloads the model. Also needs `brew install espeak-ng` for
unusual words.

Pairs with [n0b ai transcribe](transcribe.md) and `tell <agent> --attach out.m4a`.
