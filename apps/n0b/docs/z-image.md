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

n0b ai image --install    # one-time: clone repo, venv, weights
```

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

First run of `n0b ai image --install` clones `~/repos/Z-Image`, creates a venv,
installs diffusers + PyTorch (CUDA/MPS/CPU), and downloads weights (~6B params).

Uses MPS on Apple Silicon when available.
