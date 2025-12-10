# ltx-video

Wrapper script for generating videos using LTX-Video with intelligent argument parsing and automatic GPU acceleration.

## Quick Start

```bash
# Basic text-to-video
ltx-video "a cat playing with yarn"

# Image-to-video
ltx-video image.jpg "zoom out slowly"

# Custom output filename
ltx-video "spinning cube" cube.mp4

# Override defaults
ltx-video --num_frames 121 "longer video of a sunset"
```

## Usage

```bash
ltx-video [options] [prompt text...] [output.mp4]
```

## Argument Parsing

The script intelligently parses arguments:

- **Text arguments**: Concatenated with spaces to form the prompt
  - `ltx-video hello world` → prompt: "hello world"
- **Single dash arguments**: Added to negative prompt (things to avoid)
  - `ltx-video "cat playing" -yarn -red` → negative_prompt: "yarn, red"
- **File paths**: Used as reference image for image-to-video generation
  - `ltx-video photo.jpg "zoom in"` → uses photo.jpg as starting frame
- **Options starting with `--`**: Passed directly to inference.py
  - `ltx-video --num_frames 121 "long video"`
- **Last argument ending in `.mp4`**: Output filename
  - `ltx-video "test" output.mp4` → saves as output.mp4
  - Default: `YYYY-MM-DD-HHMMSS-ltx-video.mp4`

## Default Parameters

- **Model**: `ltxv-2b-0.9.8-distilled` (fastest, lowest VRAM)
- **Resolution**: 480x704 (optimized for macOS)
- **Duration**: 73 frames (3 seconds at 24 FPS)
- **Frame Rate**: 24 FPS
- **GPU**: Automatically detects and uses MPS (macOS) or CUDA (Windows/Linux)

## Examples

### Basic Generation
```bash
ltx-video "a serene mountain lake at sunset"
```

### Image-to-Video
```bash
ltx-video vacation.jpg "camera pans left revealing the ocean"
```

### Custom Output Filename
```bash
ltx-video "robot dancing" robot-dance.mp4
```

### Longer Video (5 seconds)
```bash
ltx-video --num_frames 121 "waves crashing on shore"
```

### Higher Resolution
```bash
# Warning: may require more VRAM
ltx-video --height 512 --width 896 "city street at night"
```

### Custom Seed for Reproducibility
```bash
ltx-video --seed 42 "abstract art" art-v1.mp4
```

### Using Negative Prompts
```bash
# Avoid specific elements
ltx-video "beautiful landscape" -people -buildings -cars

# Generate cat without yarn
ltx-video "cat playing" -yarn
```

### Multiple Words in Prompt
```bash
# All these are equivalent:
ltx-video hello world test
ltx-video "hello world test"
ltx-video hello "world test"
```

## Advanced Options

All inference.py options are supported. Common ones include:

- `--num_frames NUM`: Number of frames (must be 8*N + 1, e.g., 9, 17, 25, 49, 73, 121, 257)
- `--frame_rate FPS`: Frame rate (default: 24)
- `--height HEIGHT`: Video height in pixels (must be divisible by 32)
- `--width WIDTH`: Video width in pixels (must be divisible by 32)
- `--seed SEED`: Random seed for reproducibility
- `--negative_prompt TEXT`: Negative prompt for undesired features
- `--offload_to_cpu`: Enable CPU offloading for lower VRAM usage
- `--pipeline_config PATH`: Use different model config

For full list, run:
```bash
cd ~/repos/LTX-Video
source venv/bin/activate
python inference.py --help
```

## Troubleshooting

### Out of Memory Errors

If you get OOM errors, try these in order:

1. Reduce resolution:
   ```bash
   ltx-video --height 384 --width 640 "your prompt"
   ```

2. Reduce frames:
   ```bash
   ltx-video --num_frames 49 "your prompt"
   ```

3. Enable CPU offloading:
   ```bash
   ltx-video --offload_to_cpu "your prompt"
   ```

### Script Not Found

Make sure `~/bin` is in your PATH. Run:
```bash
source ~/bin/install.sh
```

### "LTX-Video repository not found"

The script looks for LTX-Video in:
- `~/repos/LTX-Video`
- `~/LTX-Video`

Install it following the setup guide below.

## Technical Details

- The script creates a temporary directory for inference output
- After generation completes, the video is moved to the desired filename
- The temporary directory path is logged in case manual inspection is needed
- GPU acceleration (MPS/CUDA) is automatically detected and enabled
- The venv is automatically activated if present in the LTX-Video directory

---

# LTX-Video Setup Guide

Complete instructions for installing and configuring LTX-Video with GPU acceleration.

## Prerequisites

### Windows
- Windows 10/11
- NVIDIA GPU (tested with RTX 4060 Ti)
- Python 3.10+ (tested with 3.13)
- Git
- NVIDIA CUDA drivers installed

### macOS
- macOS with Apple Silicon (tested on M4 Pro with 16-core GPU)
- Python 3.10+ (tested with 3.13.7)
- Git
- Sufficient unified memory (36GB recommended for larger videos)

## Installation Steps

### 1. Clone the Repository

**Windows (PowerShell):**
```powershell
cd ~/repos
git clone https://github.com/Lightricks/LTX-Video.git
cd LTX-Video
```

**macOS (zsh):**
```bash
cd ~/repos
git clone https://github.com/Lightricks/LTX-Video.git
cd LTX-Video
```

### 2. Create Virtual Environment

**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

**macOS (zsh):**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

First, install the base dependencies:

**Windows (PowerShell):**
```powershell
python -m pip install -e .[inference]
```

**macOS (zsh):**
```bash
python -m pip install -e '.[inference]'
```

Note: On macOS with zsh, the brackets need to be quoted to avoid glob expansion.

### 4. Install PyTorch with GPU Support

#### Windows: Install PyTorch with CUDA Support (CRITICAL)

**This is the most important step.** The default PyTorch installation is CPU-only and will not use your GPU.

Uninstall the CPU-only version:

```powershell
pip uninstall -y torch torchvision torchaudio
```

Install PyTorch with CUDA 11.8 support:

```powershell
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```

#### macOS: Verify MPS Support

On macOS, PyTorch is installed with MPS (Metal Performance Shaders) support by default. No additional installation needed.

### 5. Verify GPU is Working

#### Windows: Verify CUDA

Run this command to confirm PyTorch can see your GPU:

```powershell
python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}'); print(f'GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"None\"}')"
```

Expected output:
```
CUDA available: True
GPU: NVIDIA GeForce RTX 4060 Ti
```

If CUDA is **not** available, your inference will run on CPU and take hours instead of minutes.

#### macOS: Verify MPS

Run this command to confirm PyTorch can see your GPU:

```bash
python -c "import torch; print(f'PyTorch version: {torch.__version__}'); print(f'MPS available: {torch.backends.mps.is_available()}'); print(f'MPS built: {torch.backends.mps.is_built()}')"
```

Expected output:
```
PyTorch version: 2.9.1
MPS available: True
MPS built: True
```

## Running Inference

### Available Models

The repository includes several model configurations in the `configs/` directory:

- `ltxv-13b-0.9.8-dev.yaml` - Highest quality, more VRAM
- `ltxv-13b-0.9.8-distilled.yaml` - Balanced quality and speed
- `ltxv-2b-0.9.8-distilled.yaml` - **Fastest, lowest VRAM** (recommended for RTX 4060 Ti)
- FP8 variants available for even lower VRAM usage

### Basic Text-to-Video

```powershell
python inference.py --prompt "Christmas tree" --pipeline_config configs/ltxv-2b-0.9.8-distilled.yaml
```

### With Custom Parameters

```powershell
python inference.py `
  --prompt "Your prompt here" `
  --pipeline_config configs/ltxv-2b-0.9.8-distilled.yaml `
  --height 704 `
  --width 1216 `
  --num_frames 121 `
  --seed 171198
```

### Image-to-Video

```powershell
python inference.py `
  --prompt "Your prompt" `
  --conditioning_media_paths path/to/image.jpg `
  --conditioning_start_frames 0 `
  --pipeline_config configs/ltxv-2b-0.9.8-distilled.yaml
```

### Time the Execution

```powershell
Measure-Command { python inference.py --prompt "Christmas tree" --pipeline_config configs/ltxv-2b-0.9.8-distilled.yaml }
```

### Running Larger Models with Limited Memory

If you want to run the 13B model but have limited GPU memory, use the `--offload_to_cpu` flag:

**Windows (PowerShell):**
```powershell
python inference.py `
  --prompt "Your prompt" `
  --pipeline_config configs/ltxv-13b-0.9.8-distilled.yaml `
  --height 480 `
  --width 704 `
  --num_frames 49 `
  --offload_to_cpu
```

**macOS (zsh):**
```bash
python inference.py \
  --prompt "Your prompt" \
  --pipeline_config configs/ltxv-13b-0.9.8-distilled.yaml \
  --height 480 \
  --width 704 \
  --num_frames 49 \
  --offload_to_cpu
```

This keeps parts of the model on CPU and only loads them to GPU when needed, allowing larger models to run with less GPU memory at the cost of slower generation.

### Force CPU-Only Rendering

For systems with lots of RAM but no GPU (or if you want to use CPU instead of GPU), you can force CPU-only rendering:

**Windows (PowerShell):**
```powershell
$env:CUDA_VISIBLE_DEVICES = "-1"
python inference.py --prompt "Your prompt" --pipeline_config configs/ltxv-13b-0.9.8-dev.yaml
```

**macOS (zsh):**
```bash
export PYTORCH_ENABLE_MPS_FALLBACK=1
python inference.py --prompt "Your prompt" --pipeline_config configs/ltxv-13b-0.9.8-dev.yaml
```

**Note:** CPU-only rendering is 10-50x slower than GPU but can handle very large models if you have sufficient system RAM (200GB+ recommended for highest quality).

## Performance Benchmarks

### Windows

**Test System:** Windows 11, NVIDIA RTX 4060 Ti, PyTorch 2.7.1+cu118

- **2B Distilled Model** (704x1216x121 frames): ~40 minutes
- **CPU-only** (before CUDA fix): Multiple hours (not recommended)

### macOS

**Test System:** macOS, Apple M4 Pro (16-core GPU), PyTorch 2.9.1, Unified Memory

- **2B Distilled Model** (480x704x49 frames): ~2 minutes 15 seconds
- **2B Distilled Model** (512x896x81 frames): ~3 minutes 45 seconds
- **13B Distilled Model with --offload_to_cpu** (480x704x49 frames): ~15 minutes
- **2B Distilled Model** (704x1216x121 frames): Out of memory error

## Output Location

Generated videos are saved to:
```
outputs/YYYY-MM-DD/video_output_*.mp4
```

## Platform-Specific Notes

### Windows with CUDA

- First run will download model weights (~2-3GB for 2B model, ~13GB for 13B model)
- Resolution must be divisible by 32
- Number of frames must be divisible by 8, plus 1 (e.g., 9, 17, 25, 121, 257)
- Default frame rate is 30 FPS
- If you see "CUDA is not available" warning, follow step 4 to reinstall PyTorch with CUDA support

### macOS with MPS

- First run will download model weights:
  - 2B model: ~6.34GB
  - 13B model: ~13GB+
  - Text encoder: ~19GB (shared across models)
  - Spatial upscaler: ~505MB
  - Prompt enhancement models: ~6-7GB
- Resolution must be divisible by 32
- Number of frames must be divisible by 8, plus 1 (e.g., 9, 17, 25, 49, 81, 121, 257)
- Default frame rate is 30 FPS
- The model uses `bfloat16` precision by default on MPS
- PyTorch 2.4.1 works best on Apple Silicon (avoid 2.5+ which can cause noise)
- You may see "CUDA is not available" warning - this is expected. The model automatically uses MPS.

### Recommended Resolutions for M4 Pro

Based on testing, these resolutions work well on M4 Pro:

- **Small/Fast**: 480x704 with 49 frames (~2 min)
- **Medium**: 512x896 with 81 frames (~3.5 min)
- **Large**: May require reducing frames or using FP8 variant

Note: The default 704x1216 resolution with 121 frames exceeds memory capacity on M4 Pro. Use smaller dimensions or fewer frames.

## Troubleshooting

### Windows: "CUDA is not available" Warning

This means PyTorch is using the CPU-only version. Follow step 4 to reinstall PyTorch with CUDA support.

### macOS: "CUDA is not available" Warning

This is expected on macOS. The model automatically falls back to MPS (Metal Performance Shaders) for GPU acceleration. You can safely ignore this warning.

### macOS: "Invalid buffer size" Error

This means the video dimensions or frame count exceed available unified memory. Try these in order:
1. Use `--offload_to_cpu` flag to enable CPU offloading
2. Reduce resolution (e.g., from 704x1216 to 512x896 or 480x704)
3. Reduce frame count (e.g., from 121 to 81 or 49)
4. Use an FP8 variant (e.g., `ltxv-2b-0.9.8-distilled-fp8.yaml`)
5. Use the 2B model instead of 13B

### "Pipeline config file does not exist"

Use `--pipeline_config` with a valid config file from the `configs/` directory. Do not use the default path shown in the error.

### Out of Memory Errors

Try these in order:
1. Add `--offload_to_cpu` flag to enable CPU offloading
2. Use the 2B distilled model instead of 13B
3. Use an FP8 variant (e.g., `ltxv-2b-0.9.8-distilled-fp8.yaml`)
4. Reduce resolution with `--height` and `--width` parameters
5. Reduce frames with `--num_frames` parameter
6. Force CPU-only rendering if you have 200GB+ system RAM

## Additional Resources

- [Official Repository](https://github.com/Lightricks/LTX-Video)
- [ComfyUI Integration](https://github.com/Lightricks/ComfyUI-LTXVideo/)
- [Model Documentation](https://github.com/Lightricks/LTX-Video#models)
- [Hugging Face Discussion on Mac Support](https://huggingface.co/Lightricks/LTX-Video/discussions/26)
