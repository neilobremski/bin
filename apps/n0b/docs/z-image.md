---
name: "n0b-image"
description: "Generate images locally with Z-Image-Turbo. Use when the user wants text-to-image or reference-guided image generation."
allowed-tools: Bash(n0b ai image *)
---

# n0b ai image

Local image generation with [Z-Image-Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo).
Text-to-image by default; pass `--ref` for img2img from a single reference image.

```bash
n0b ai image "a red fox in fresh snow"
n0b ai image photo.jpg "oil painting, warm light" --ref photo.jpg
n0b ai image "cinematic portrait" --ref face.png --strength 0.35 -o out.png
printf 'a red fox in fresh snow' | n0b ai image -

n0b ai image --install      # optional: prep the shared runtime/group (PyTorch is large)
n0b ai image --uninstall    # remove <bin>/.venv
```

## Model overrides (`--model`)

The default is `z-image` (Z-Image-Turbo, Qwen text encoder). `mistral3` selects
ERNIE-Image-Turbo, whose text encoder is Mistral 3.

You can also give a Hugging Face repository ID for a complete compatible
Diffusers pipeline, or the URL of a `.gguf` file that replaces that family's
text encoder. The GGUF's `general.architecture` picks the family (`qwen3` →
z-image, `mistral3` → ERNIE-Image-Turbo). A `mistral3` GGUF is the text
backbone only; it is loaded as Mistral, not the Mistral 3 vision model. The
encoder must keep the production hidden width (2560 for Z-Image, 3072 for
ERNIE); a smaller-parameter model with the same architecture is not compatible.
The file is downloaded once to the Hugging Face cache and reused on later runs.

```bash
n0b ai image "a studio portrait" --model mistral3
n0b ai image "a studio portrait" --model owner/z-image-variant
n0b ai image "a studio portrait" --model \
  https://huggingface.co/owner/qwen-gguf/blob/main/text-encoder.gguf
```

`--ref` is Z-Image only. A random Hugging Face model is not necessarily an
image generator.

### Small smoke-test model

Hugging Face's
[`hf-internal-testing/tiny-zimage-pipe`](https://huggingface.co/hf-internal-testing/tiny-zimage-pipe)
is a complete 18 MB Z-Image fixture for testing download and pipeline wiring:

```bash
n0b ai image "smoke test" --model hf-internal-testing/tiny-zimage-pipe \
  --width 64 --height 64 -o /tmp/n0b-tiny-zimage.png
```

Its weights are random, so noise rather than a meaningful image is the expected
result. It does not validate production image quality or encoder compatibility.

## Reference images (`--ref`)

Z-Image-Turbo supports **one** reference image via img2img. Extra `--ref` flags are
ignored with a warning. Use `--strength` to control how much changes:

| Strength | Effect |
|----------|--------|
| 0.15–0.30 | Polish — keep composition, tweak finish |
| 0.35–0.50 | Restyle — structure holds, vibe shifts |
| 0.60+ | Reimagine — loose guide only |

Default strength is `0.6`. `--strength` without `--ref` is an error.

## Output

`-o` / `--out` sets the PNG path. Default: `z-image-<timestamp>.png` in the
current directory.

## Setup

First use creates a clean runtime under `<bin>/.venv/runtime` and atomically
installs `requirements/ai*.txt` under an ABI/platform-keyed dependency group.
Image, speech, and standard Whisper share the AI group, so PyTorch installs
once; incompatible groups such as MLX remain isolated. Uses MPS on Apple
Silicon or CUDA when available.

`--install` runs setup without generating. `--uninstall` removes the shared
substrate and legacy per-command caches.
