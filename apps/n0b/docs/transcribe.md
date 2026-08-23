---
name: "n0b-transcribe"
description: "Transcribe audio or video to text with local Whisper; video gets a fancy narrative via Ollama vision. Use when the user provides a recording or video and wants its text."
allowed-tools: Bash(n0b ai transcribe *)
---

# n0b ai transcribe

Local speech-to-text. Default engine is `auto`: on Apple Silicon that picks
`mlx-whisper`; elsewhere it uses openai-whisper (`whisper`). Override with
`--engine`, including `parakeet-mlx` (NVIDIA Parakeet on MLX). No API key;
models run on this machine. For files with a video stream, `--flavor auto`
(default) also samples frames and asks a local Ollama vision model for a
Surfey-style fancy narrative (synopsis, timed scenes, summary).

Transcription goes to stdout; everything else (setup, progress) to stderr,
so output is pipe-safe.

## Usage

```bash
n0b ai transcribe memo.m4a
n0b ai transcribe memo.m4a > memo.txt

n0b ai transcribe clip.mp4                 # auto → fancy when video stream present
n0b ai transcribe clip.mp4 --flavor plain  # Whisper text only
n0b ai transcribe memo.m4a --flavor fancy  # errors: no video stream

n0b ai transcribe memo.m4a --engine mlx-whisper     # force MLX Whisper
n0b ai transcribe memo.m4a --engine parakeet-mlx    # NVIDIA Parakeet on MLX
n0b ai transcribe memo.m4a --engine whisper         # openai-whisper (PyTorch)

n0b ai transcribe memo.m4a --hint "Pay-i" --hint "a8s, r4t"   # bias proper nouns
n0b ai transcribe memo.m4a --language en                       # skip auto-detect
n0b ai transcribe memo.m4a --model base                        # smaller/faster Whisper

n0b ai transcribe clip.mp4 --vision-model qwen3.6              # Ollama vision model
n0b ai transcribe clip.mp4 --vision-model qwen3.5:4b

n0b ai transcribe --hint "Pay-i, k7e" --save                   # add to the global hints file
n0b ai transcribe memo.m4a --hint "Pay-i" --save               # save, then transcribe

n0b ai transcribe memo.m4a --replace 'Jerry => Gerry'          # annotate known mis-hearings
n0b ai transcribe --replace 'Jerry => Gerry' --save            # add to the global replacements file

n0b ai transcribe memo.m4a --condition-on-previous             # old Whisper feedback (can loop)
```

stderr reports the flavor in effect, hints, model loading, and progress so a
long silence is never ambiguous.

Any format ffmpeg can read works: m4a, mp3, wav, aiff, ogg, mp4, ...

## Engines

`--engine` selects the STT backend:

| Value | Behavior |
|-------|----------|
| `auto` (default) | `mlx-whisper` when Apple Silicon MLX is available; else `whisper` |
| `whisper` | [openai-whisper](https://github.com/openai/whisper) (PyTorch) |
| `mlx-whisper` | [mlx-whisper](https://pypi.org/project/mlx-whisper/) on Apple Silicon |
| `parakeet-mlx` | [parakeet-mlx](https://github.com/senstella/parakeet-mlx) (NVIDIA Parakeet) |

`parakeet-mlx` always chunks long audio (`chunk_duration=120`,
`overlap_duration=15`) to avoid Metal OOM on hour-scale files.

Short Whisper size names (`tiny`…`turbo`) map to `mlx-community/whisper-*`
HF repos under `mlx-whisper`; unknown short names are rejected. For
`parakeet-mlx`, Whisper short names resolve to
`mlx-community/parakeet-tdt-0.6b-v3`; pass a full HF repo to pick another
Parakeet checkpoint. Hints, `--language`, and `--condition-on-previous` apply
to Whisper engines only.

## Flavor

`--flavor` controls output style:

| Value | Behavior |
|-------|----------|
| `auto` (default) | Fancy if ffprobe finds a video stream; else plain Whisper text |
| `plain` | Always Whisper text (works on video audio tracks too) |
| `fancy` | Always fancy narrative; requires a video stream |

Fancy path: extract ≤50 frames at ≤1 FPS (scaled to 512px), timed Whisper
speech, then one Ollama `/api/chat` call with those images + the Surfey-style
prompt. Ollama must already be running (`ollama serve`).

## Vision model

`--vision-model` (or `N0B_TRANSCRIBE_VISION_MODEL`, default `qwen3.6`) selects
the Ollama model. Capabilities are checked via `/api/show`; if the model is
missing or lacks `"vision"`, the command fails with a clear pull hint:

```text
n0b ai transcribe: model 'qwen3:4b' has no vision support; try: ollama pull qwen3.6
```

No auto-pull. Override the Ollama host with `OLLAMA_URL` if needed.

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
"Jerry"). Replacements run **after** Whisper (and before the vision call for
fancy): each is a regex plus the likely correction, and every match gets
annotated in place —

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
use `base` when speed matters more than proper nouns. With `mlx-whisper`,
those names map to Hugging Face MLX repos; with `parakeet-mlx`, pass a
Parakeet HF id (or leave the Whisper default to get the Parakeet default).

## Silence-conditioning loops

By default Whisper does **not** condition each window on its previous text
(`condition_on_previous_text=False`). The library default of `True` feeds
decoder output back into itself and can sustain repetition loops on silence
or noise ("Thank you. Thank you. …"). Pass `--condition-on-previous` only if
you want the old behaviour for cleaner continuous speech.

This does **not** eliminate all Whisper loops — within-window token
repetition on long noisy audio can still happen. When the output has four or
more identical short consecutive sentences, stderr warns so you can retry
with `--engine parakeet-mlx` (often cleaner on that class).

## First run

Bootstraps the clean runtime and isolated dependency groups under `<bin>/.venv`
on first use, then downloads the STT model. Standard Whisper shares the AI group
(so torch installs once); MLX engines use `requirements/ai-mlx.txt` in a separate
group (`mlx-whisper`, `parakeet-mlx`). One-time cost of a
few GB; subsequent runs are offline for plain flavor. Fancy also needs a local
vision model via Ollama. Requires `ffmpeg` (and `ffprobe` for flavor detection
/ fancy) on PATH.
