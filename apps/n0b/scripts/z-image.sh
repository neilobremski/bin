#!/usr/bin/env bash

# z-image: Wrapper script for generating images with Z-Image
# Usage: z-image [options] prompt text
# Options starting with - or -- are passed to Python script
# Text arguments without dashes become the prompt

set -e

# Install function
do_install() {
  echo "=== Z-Image Installation ==="
  
  # Determine install location
  if [ -d "$HOME/repos" ]; then
    INSTALL_DIR="$HOME/repos"
  else
    INSTALL_DIR="$HOME"
  fi
  
  TARGET_DIR="$INSTALL_DIR/Z-Image"
  
  # Clone if not already present
  if [ -d "$TARGET_DIR" ]; then
    echo "Z-Image repo already exists at $TARGET_DIR"
    echo "Pulling latest changes..."
    cd "$TARGET_DIR"
    git pull
  else
    echo "Cloning Z-Image to $TARGET_DIR..."
    git clone https://github.com/Tongyi-MAI/Z-Image.git "$TARGET_DIR"
    cd "$TARGET_DIR"
  fi
  
  # Create venv if it doesn't exist
  if [ -d "$TARGET_DIR/venv" ]; then
    echo "Virtual environment already exists"
  else
    echo "Creating Python virtual environment..."
    python3 -m venv "$TARGET_DIR/venv"
  fi
  
  # Activate venv
  source "$TARGET_DIR/venv/bin/activate"
  
  # Upgrade pip
  echo "Upgrading pip..."
  pip install --upgrade pip
  
  # Install diffusers from source (required for Z-Image support)
  echo "Installing diffusers from source (required for Z-Image)..."
  pip install git+https://github.com/huggingface/diffusers
  
  # Install PyTorch with appropriate backend
  echo "Detecting GPU backend..."
  
  if command -v n0b &> /dev/null && n0b gpu cuda 2>/dev/null; then
    echo "CUDA detected - installing PyTorch with CUDA support..."
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
  elif command -v n0b &> /dev/null && n0b gpu mps 2>/dev/null; then
    echo "MPS (Apple Silicon) detected - PyTorch already has MPS support"
    pip install torch torchvision
  else
    echo "No GPU detected - using CPU-only PyTorch"
    pip install torch torchvision
  fi
  
  # Install other dependencies
  echo "Installing additional dependencies..."
  pip install transformers accelerate loguru pillow
  
  echo ""
  echo "=== Installation Complete ==="
  echo "Z-Image installed at: $TARGET_DIR"
  echo "Virtual environment: $TARGET_DIR/venv"
  echo ""
  echo "You can now run: z-image \"your prompt here\""
  
  exit 0
}

# Check for --install flag
for arg in "$@"; do
  if [ "$arg" = "--install" ]; then
    do_install
  fi
done

# Check if Z-Image repo exists
ZIMAGE_DIR="$HOME/repos/Z-Image"
if [ ! -d "$ZIMAGE_DIR" ]; then
  echo "Error: Z-Image repository not found at $ZIMAGE_DIR"
  echo "Run: z-image --install"
  exit 1
fi

# Default parameters
OUTPUT_FILE=""
PROMPT=""
WIDTH=""
HEIGHT=""
REF_FILE=""
STRENGTH="0.6"
EXTRA_ARGS=()

# Parse arguments
SKIP_NEXT=false
ARGS=("$@")

for i in "${!ARGS[@]}"; do
  if [ "$SKIP_NEXT" = true ]; then
    SKIP_NEXT=false
    continue
  fi
  
  arg="${ARGS[$i]}"
  next_arg="${ARGS[$((i+1))]:-}"
  
  # Handle --output or -o flag
  if [ "$arg" = "--output" ] || [ "$arg" = "-o" ]; then
    if [ -n "$next_arg" ]; then
      OUTPUT_FILE="$next_arg"
      SKIP_NEXT=true
    else
      echo "Error: $arg requires a filename"
      exit 1
    fi
  # Handle --width flag
  elif [ "$arg" = "--width" ]; then
    if [ -n "$next_arg" ]; then
      WIDTH="$next_arg"
      SKIP_NEXT=true
    else
      echo "Error: --width requires a value"
      exit 1
    fi
  # Handle --height flag
  elif [ "$arg" = "--height" ]; then
    if [ -n "$next_arg" ]; then
      HEIGHT="$next_arg"
      SKIP_NEXT=true
    else
      echo "Error: --height requires a value"
      exit 1
    fi
  # Handle --16:9 shortcut (dimensions must be divisible by 16)
  elif [ "$arg" = "--16:9" ]; then
    WIDTH="1920"
    HEIGHT="1088"
  # Handle --ref reference image
  elif [ "$arg" = "--ref" ]; then
    if [ -n "$next_arg" ]; then
      if [ -n "$REF_FILE" ]; then
        echo "Warning: Z-Image-Turbo supports one reference image; ignoring extra --ref" >&2
      else
        REF_FILE="$next_arg"
      fi
      SKIP_NEXT=true
    else
      echo "Error: --ref requires a file path"
      exit 1
    fi
  # Handle --strength
  elif [ "$arg" = "--strength" ]; then
    if [ -n "$next_arg" ]; then
      STRENGTH="$next_arg"
      SKIP_NEXT=true
    else
      echo "Error: --strength requires a value"
      exit 1
    fi
  # Check if it starts with a dash (option to pass through)
  elif [[ "$arg" =~ ^-.+ ]]; then
    EXTRA_ARGS+=("$arg")
  # Otherwise it's part of the prompt
  else
    if [ -z "$PROMPT" ]; then
      PROMPT="$arg"
    else
      PROMPT="$PROMPT $arg"
    fi
  fi
done

if [ -n "$REF_FILE" ] && [ ! -f "$REF_FILE" ]; then
  echo "Error: reference image not found: $REF_FILE"
  exit 1
fi

if [ "$STRENGTH" != "0.6" ] && [ -z "$REF_FILE" ]; then
  echo "Error: --strength requires --ref"
  exit 1
fi

# Generate default output filename if not specified
if [ -z "$OUTPUT_FILE" ]; then
  TIMESTAMP=$(date +"%Y-%m-%d-%H-%M-%S")
  OUTPUT_FILE="z-image-${TIMESTAMP}.png"
fi

# Activate venv
if [ -d "$ZIMAGE_DIR/venv" ]; then
  source "$ZIMAGE_DIR/venv/bin/activate"
else
  echo "Error: Virtual environment not found at $ZIMAGE_DIR/venv"
  echo "Run: cd $ZIMAGE_DIR && python3 -m venv venv && source venv/bin/activate && pip install -e ."
  exit 1
fi

# Create temporary Python script to run inference
TEMP_SCRIPT=$(mktemp)
cat > "$TEMP_SCRIPT" << 'EOFPYTHON'
import os
import sys
import warnings

import torch

warnings.filterwarnings("ignore")

def device_for() -> str:
  if torch.cuda.is_available():
    return "cuda"
  if torch.backends.mps.is_available():
    return "mps"
  return "cpu"


def model_repo() -> str:
  local = os.path.join(os.environ["ZIMAGE_DIR"], "ckpts/Z-Image-Turbo")
  if os.path.isdir(local):
    return local
  return "Tongyi-MAI/Z-Image-Turbo"


def run_img2img(prompt, output_file, ref_file, width, height, strength):
  from diffusers import ZImageImg2ImgPipeline
  from PIL import Image

  dtype = torch.bfloat16
  device = device_for()
  pipe = ZImageImg2ImgPipeline.from_pretrained(model_repo(), torch_dtype=dtype)
  pipe.to(device)

  init_image = Image.open(ref_file).convert("RGB").resize((width, height))
  generator = None
  image = pipe(
    prompt,
    image=init_image,
    strength=float(strength),
    height=height,
    width=width,
    num_inference_steps=8,
    guidance_scale=0.0,
    generator=generator,
  ).images[0]
  image.save(output_file)
  print(f"\nImage saved to: {output_file}")


def run_text2img(prompt, output_file, width, height):
  sys.path.insert(0, os.path.join(os.environ["ZIMAGE_DIR"], "src"))
  from utils import ensure_model_weights, load_from_local_dir, set_attention_backend
  from zimage import generate

  model_path = ensure_model_weights(
    os.path.join(os.environ["ZIMAGE_DIR"], "ckpts/Z-Image-Turbo"), verify=False
  )
  dtype = torch.bfloat16
  device = device_for()
  components = load_from_local_dir(model_path, device=device, dtype=dtype, compile=False)
  set_attention_backend(os.environ.get("ZIMAGE_ATTENTION", "_native_flash"))
  images = generate(
    prompt=prompt,
    **components,
    height=height,
    width=width,
    num_inference_steps=8,
    guidance_scale=0.0,
    generator=None,
  )
  images[0].save(output_file)
  print(f"\nImage saved to: {output_file}")


def main():
  prompt = os.environ.get("PROMPT", "")
  output_file = os.environ.get("OUTPUT_FILE", "output.png")
  ref_file = os.environ.get("REF_FILE", "")
  strength = os.environ.get("STRENGTH", "0.6")
  width = int(os.environ.get("WIDTH") or "1024")
  height = int(os.environ.get("HEIGHT") or "1024")

  if not prompt:
    print("Error: No prompt provided")
    sys.exit(1)

  if ref_file:
    print(f"img2img: ref={ref_file} strength={strength}", file=sys.stderr)
    run_img2img(prompt, output_file, ref_file, width, height, strength)
  else:
    run_text2img(prompt, output_file, width, height)

if __name__ == "__main__":
  main()
EOFPYTHON

# Export variables for Python script
export ZIMAGE_DIR
export PROMPT
export OUTPUT_FILE
export WIDTH
export HEIGHT
export REF_FILE
export STRENGTH

# Run the Python script
python "$TEMP_SCRIPT"

# Clean up
rm "$TEMP_SCRIPT"
