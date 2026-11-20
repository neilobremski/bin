---
name: "n0b-speak"
description: "Read text aloud or save speech to a file across local and secret-gated cloud TTS vendors. Use when the user wants to hear text spoken or create an audio attachment."
allowed-tools: Bash(n0b ai speak *)
---

# n0b ai speak

Text-to-speech for inline text, files, or stdin across local engines (`say`,
`kokoro`, `pocket`) and secret-gated cloud vendors (`openai`, `elevenlabs`,
`cartesia`).

On macOS `auto` defaults to `say(1)`; elsewhere it defaults to local `kokoro`.
`auto` never spends paid cloud credits unasked, even when vendor keys exist in
`n0b secrets`. Select a cloud vendor explicitly with `--vendor` / `--engine`,
a `vendor:voice` preference entry, or a sticky `--save` setting.

```bash
n0b ai speak "hello"                                      # play on speakers
echo "ship it" | n0b ai speak                             # pipe stdin
n0b ai speak notes.md -o notes.m4a                        # save file (no playback)
n0b ai speak -v Samantha "hi" --save                      # sticky macOS voice

n0b ai speak notes.md --engine kokoro -o out.wav          # local Kokoro-82M
n0b ai speak notes.md --vendor pocket -v "Ember, Male" -o out.wav
n0b ai speak notes.md --vendor openai -v marin -o out.wav # uses OPENAI_API_KEY
n0b ai speak notes.md -v "openai:marin, pocket:alba, af_heart" -o out.wav

n0b ai speak notes.md --pause-major 1.5 --pause-minor 0.8
n0b ai speak notes.md --flat                              # single-pass cleanup
n0b ai speak --replace '\ba8s\b => A eight S' --save
n0b ai speak --pronounce 'Pay-i => pˈeɪ ˈaɪ' --save       # kokoro only
```

## Output modes

| Invocation | Behavior |
|------------|----------|
| No `-o` | Play on speakers (`afplay`, `aplay`, `ffplay`, `paplay`, `pw-play`) |
| `-o path` | Write audio file only (stdout prints the path) |

- **`.wav`**: stdlib 24 kHz 16-bit mono PCM WAV across all neural/cloud engines
  (`say` converts via `afconvert` or `ffmpeg`).
- **`.m4a` / `.aac` / `.mp4`**: native on `say`; encoded via `afconvert`
  (macOS) or `ffmpeg` elsewhere.
- **`.mp3`**: encoded via `ffmpeg`.

## Vendors and engines (`--engine` / `--vendor`)

| Engine | Secret (`n0b secrets`) | Default model | Default voice |
|--------|------------------------|---------------|---------------|
| `auto` | none (local only) | — | `say` on macOS, else `kokoro` |
| `say` | none | macOS system | system default |
| `kokoro` | none (`ai` venv group) | Kokoro-82M | `af_heart` |
| `pocket` | none (`pocket-tts` venv group) | `pocket-tts==4.0.1` | `alba` |
| `openai` | `OPENAI_API_KEY` | `gpt-realtime-2.1-mini` | `marin` |
| `elevenlabs` | `ELEVENLABS_API_KEY` | `eleven_flash_v2_5` | `Rachel` (`21m00Tcm4TlvDq8ikWAM`) |
| `cartesia` | `CARTESIA_API_KEY` | `sonic-3.6` | `Katie` (`f786b574-daa5-4673-aa0c-cbe3e8534c02`) |

### OpenAI GA Realtime synthesis

`--vendor openai` connects directly over stdlib TLS WebSocket to the GA
Realtime API (`wss://api.openai.com/v1/realtime`, default model
`gpt-realtime-2.1-mini`, override with `--model`) in verbatim text-readout
mode (`conversation: "none"`, 24 kHz PCM16). Legacy `/v1/audio/speech` models
(`tts-1`, `tts-1-hd`, `gpt-4o-mini-tts`, sunsetting January 6, 2027) are
rejected with a clear error.

### Pocket TTS

`--vendor pocket` installs `pocket-tts==4.0.1` into its own isolated
`<bin>/.venv` group (`pocket-tts`), verifies the pinned model, tokenizer, and
selected voice embedding against SHA-256 digests, and runs offline across
sentence-bounded 300-character chunks.

## Voice selection and fallback lists (`-v` / `--voice`)

`-v` accepts a single voice or a CSS font-stack comma-separated preference
list resolved left to right:

1. **Vendor-qualified entries** (`openai:marin, pocket:alba, af_heart`) skip
   any vendor whose `n0b` secret or local tool is unavailable and select the
   first available match.
2. **Per-catalog matching** tries exact ID/name/alias, then prefix, then
   substring (case- and separator-insensitive), breaking ties by catalog order.
3. **`Male` and `Female`** match recorded source gender metadata only (never
   substring matching). On `pocket`, `Male` selects `charles` and `Female`
   selects `anna`.
4. **Kokoro blends** (`af_nicole,af_bella`) and non-English Kokoro IDs
   (`jf_alpha`) pass straight to Kokoro unchanged.
5. **Custom cloud voice IDs** (single CLI `-v <id>` on `openai`, `elevenlabs`,
   or `cartesia`) pass through when not in the built-in preset list.

Persist defaults with `--save`:

- `--vendor NAME --save` → `~/.config/n0b/speak-engine.txt` (`--vendor auto --save` clears it)
- `-v SPEC --save` → `~/.config/n0b/speak-voice.txt`

## Input and Markdown prosody

Plain text is spoken after optional replacements. Markdown is detected
automatically and spoken in sections across every engine:

| Boundary | Flag | Default |
|----------|------|---------|
| Before H1 / H2 | `--pause-major` | `2.0` s |
| Before H3–H6 | `--pause-minor` | `1.0` s |
| Between paragraphs | `--pause-para` | `0.4` s |

Use `--flat` for single-pass `speakable()` cleanup without section pauses,
`--raw` to skip cleanup, and `--no-emphasis` to disable bold/code speed tweaks.
Code fences and table rows are skipped.

## Replacements and pronunciations

- `~/.config/n0b/speak-replacements.txt` / `--replace 'PATTERN => SPOKEN'` —
  applied before synthesis for every engine.
- `~/.config/n0b/speak-pronunciations.txt` / `--pronounce 'WORD => IPA'` —
  Misaki `[word](/ipa/)` phoneme overrides for `kokoro` (flattened to plain
  words on other engines).

Pairs with [n0b secrets](secrets.md), [n0b ai transcribe](transcribe.md), and
`tell <agent> --attach out.m4a`.
