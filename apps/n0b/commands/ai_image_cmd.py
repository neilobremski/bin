"""n0b ai image — Z-Image-Turbo text-to-image and img2img."""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

from ai_venv import AI_VENV, ensure_image, uninstall as ai_venv_uninstall

ZIMAGE_MODEL = "Tongyi-MAI/Z-Image-Turbo"

_ZIMAGE_SNIPPET = """\
import sys
import warnings

import torch

warnings.filterwarnings("ignore")

prompt, out_path, ref_path, strength, width, height = sys.argv[1:7]
width, height = int(width), int(height)

if torch.cuda.is_available():
    device = "cuda"
elif torch.backends.mps.is_available():
    device = "mps"
else:
    device = "cpu"
dtype = torch.bfloat16

if ref_path:
    from diffusers import ZImageImg2ImgPipeline
    from PIL import Image

    print(f"img2img: ref={ref_path} strength={strength}", file=sys.stderr)
    pipe = ZImageImg2ImgPipeline.from_pretrained(%r, torch_dtype=dtype)
    pipe.to(device)
    init = Image.open(ref_path).convert("RGB").resize((width, height))
    image = pipe(
        prompt,
        image=init,
        strength=float(strength),
        height=height,
        width=width,
        num_inference_steps=8,
        guidance_scale=0.0,
    ).images[0]
else:
    from diffusers import ZImagePipeline

    print("loading Z-Image-Turbo...", file=sys.stderr)
    pipe = ZImagePipeline.from_pretrained(%r, torch_dtype=dtype)
    pipe.to(device)
    image = pipe(
        prompt,
        height=height,
        width=width,
        num_inference_steps=9,
        guidance_scale=0.0,
    ).images[0]

image.save(out_path)
print(out_path)
""" % (ZIMAGE_MODEL, ZIMAGE_MODEL)


def resolve_image_ref(
    refs: list[str],
) -> tuple[str | None, str | None, str | None]:
    if not refs:
        return None, None, None
    path = Path(refs[0]).expanduser()
    if not path.is_file():
        return None, None, f"n0b ai image: no such reference file: {refs[0]}"
    note = None
    if len(refs) > 1:
        note = (
            f"n0b ai image: using first --ref only ({path}); "
            "Z-Image-Turbo supports one reference image"
        )
    return str(path), note, None


def build_image_argv(
    prompt: list[str],
    refs: list[str],
    strength: float,
    out: str | None,
) -> tuple[list[str], str | None]:
    rest = list(prompt)
    if rest[:1] == ["--"]:
        rest = rest[1:]
    ref, note, err = resolve_image_ref(refs)
    if err:
        raise ValueError(err)
    if refs and not 0.0 <= strength <= 1.0:
        raise ValueError("n0b ai image: --strength must be between 0.0 and 1.0")
    if strength != 0.6 and ref is None:
        raise ValueError("n0b ai image: --strength requires --ref")
    argv: list[str] = []
    if ref is not None:
        argv.extend(["--ref", ref, "--strength", str(strength)])
    if out:
        argv.extend(["-o", out])
    argv.extend(rest)
    return argv, note


def cmd_image(
    model: str | None,
    prompt: list[str],
    refs: list[str],
    strength: float,
    out: str | None,
    width: int | None = None,
    height: int | None = None,
    aspect_16_9: bool = False,
    install: bool = False,
    uninstall: bool = False,
) -> int:
    if uninstall:
        return ai_venv_uninstall()
    if model and model != "z-image":
        print(
            f"n0b ai image: unknown model {model!r} (known: z-image)",
            file=sys.stderr,
        )
        return 1
    parts = list(prompt)
    if parts[:1] == ["--"]:
        parts = parts[1:]
    if install and not parts and not refs:
        try:
            ensure_image()
        except subprocess.CalledProcessError as exc:
            print(f"n0b ai image: setup failed: {exc}", file=sys.stderr)
            return 1
        print(f"ready: {AI_VENV}", file=sys.stderr)
        return 0
    if not parts:
        print("n0b ai image: prompt required", file=sys.stderr)
        return 2
    try:
        _, note = build_image_argv(parts, refs, strength, out)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        msg = str(exc)
        return 1 if "no such reference" in msg else 2
    if note:
        print(note, file=sys.stderr)
    ref_path, _, err = resolve_image_ref(refs)
    if err:
        print(err, file=sys.stderr)
        return 1
    prompt_text = " ".join(parts)
    if aspect_16_9:
        gen_width, gen_height = 1920, 1088
    else:
        gen_width, gen_height = width or 1024, height or 1024
    out_path = (
        Path(out).expanduser()
        if out
        else Path(f"z-image-{datetime.now():%Y-%m-%d-%H-%M-%S}.png")
    )
    try:
        python = ensure_image()
    except subprocess.CalledProcessError as exc:
        print(f"n0b ai image: setup failed: {exc}", file=sys.stderr)
        return 1
    proc = subprocess.run(
        [
            str(python),
            "-c",
            _ZIMAGE_SNIPPET,
            prompt_text,
            str(out_path),
            ref_path or "",
            str(strength),
            str(gen_width),
            str(gen_height),
        ],
    )
    return proc.returncode if proc.returncode is not None else 0
