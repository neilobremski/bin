---
name: "n0b-video"
description: "Video file utilities: extract last frame or convert a video to an animated GIF. Use when the user wants a still or GIF preview from a video file."
allowed-tools: Bash(n0b video *)
---

# n0b video

Local ffmpeg utilities for video files. Not generative (`n0b ai video` is LTX/MLX).

## last-frame

```bash
n0b video last-frame clip.mp4
n0b video last-frame clip.mp4 -o end.png
```

Extracts the final video frame as a PNG.

## gif

Convert a video to an animated GIF using a palette (Surfey-style `palettegen` /
`paletteuse=dither=sierra2_4a`).

```bash
n0b video gif clip.mp4
n0b video gif clip.mp4 -o preview.gif
n0b video gif clip.mp4 --preset thumb    # default
n0b video gif clip.mp4 --preset small
n0b video gif clip.mp4 --fps 8 --width 320 --colors 64
```

### Presets

| Preset | FPS | Width | Colors | Notes |
|--------|-----|-------|--------|-------|
| `thumb` (default) | adaptive ≤1 (≤50 frames) | 320 | 64 | Matches Surfey `thumb-gif` |
| `small` | 8 | 800 | 32 | Matches Surfey `quick-gif-small` |

`--fps`, `--width`, `--colors`, and `--max-frames` override the preset.
Default output is `<stem>.gif` in the current directory.

Requires `ffmpeg` on PATH (`ffprobe` recommended for `thumb` adaptive FPS).
