"""n0b ai image — local text-to-image and img2img."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from ai_venv import AI_VENV, ensure_image, uninstall as ai_venv_uninstall

N0B_ROOT = Path(__file__).resolve().parent.parent

IMAGE_FAMILIES = {
    "z-image": {
        "repo": "Tongyi-MAI/Z-Image-Turbo",
        "pipeline": "ZImagePipeline",
        "img2img": "ZImageImg2ImgPipeline",
        "arch": ("qwen3", "qwen2", "qwen2moe"),
        "text_dim": 2560,
        "steps": 9,
        "img2img_steps": 8,
        "guidance": 0.0,
        "call": {},
    },
    "mistral3": {
        "repo": "baidu/ERNIE-Image-Turbo",
        "pipeline": "ErnieImagePipeline",
        "img2img": None,
        "arch": ("mistral3",),
        "text_dim": 3072,
        "steps": 8,
        "img2img_steps": 8,
        "guidance": 1.0,
        "call": {"use_pe": True},
    },
}

_IMAGE_SNIPPET = (
    "import sys\n"
    f"sys.path.insert(0, {str(N0B_ROOT)!r})\n"
    + r"""
import warnings

import torch

warnings.filterwarnings("ignore")

prompt, out_path, ref_path, strength, width, height, model = sys.argv[1:8]
width, height = int(width), int(height)

if torch.cuda.is_available():
    device = "cuda"
elif torch.backends.mps.is_available():
    device = "mps"
else:
    device = "cpu"
dtype = torch.bfloat16

FAMILIES = """ + repr(IMAGE_FAMILIES) + r"""
ARCH_TO_FAMILY = {
    arch: name for name, spec in FAMILIES.items() for arch in spec["arch"]
}


def die(message):
    print(f"n0b ai image: {message}", file=sys.stderr)
    sys.exit(1)


pipeline_model = FAMILIES["z-image"]["repo"]
family = FAMILIES["z-image"]
pipeline_kwargs = {}
gguf_encoder = False

if model in FAMILIES:
    family = FAMILIES[model]
    pipeline_model = family["repo"]
elif model.startswith("https://huggingface.co/"):
    from huggingface_hub import hf_hub_download
    from commands.image_gguf import (
        gguf_architecture,
        load_gguf_text_encoder,
        parse_hub_file,
    )

    repo_id, revision, filename = parse_hub_file(model)
    print(f"downloading text encoder from {repo_id}...", file=sys.stderr)
    local = hf_hub_download(repo_id, filename, revision=revision)
    arch = gguf_architecture(local)
    family_name = ARCH_TO_FAMILY.get(arch)
    if family_name is None:
        known = ", ".join(sorted(ARCH_TO_FAMILY))
        die(f"GGUF architecture {arch!r} is not supported (known: {known})")
    family = FAMILIES[family_name]
    pipeline_model = family["repo"]
    print(f"loading {family_name} text encoder ({arch})...", file=sys.stderr)
    encoder, tokenizer = load_gguf_text_encoder(
        repo_id, filename, revision, pipeline_model, dtype, local, arch
    )
    actual_dim = getattr(encoder.config, "hidden_size", None)
    if actual_dim != family["text_dim"]:
        die(
            f"GGUF hidden size {actual_dim} is incompatible with {family_name} "
            f"(expected {family['text_dim']})"
        )
    pipeline_kwargs["text_encoder"] = encoder
    pipeline_kwargs["tokenizer"] = tokenizer
    if family_name == "mistral3":
        pipeline_kwargs["pe"] = None
        pipeline_kwargs["pe_tokenizer"] = None
    gguf_encoder = True
elif model:
    pipeline_model = model

if ref_path and not family["img2img"]:
    die(f"{family['pipeline']} does not support --ref")

import diffusers

pipe_name = family["img2img"] if ref_path else family["pipeline"]
Pipe = getattr(diffusers, pipe_name)
call = dict(family["call"])
if gguf_encoder and "use_pe" in call:
    call["use_pe"] = False

if ref_path:
    from PIL import Image

    print(f"img2img: ref={ref_path} strength={strength}", file=sys.stderr)
    pipe = Pipe.from_pretrained(pipeline_model, torch_dtype=dtype, **pipeline_kwargs)
    pipe.to(device)
    init = Image.open(ref_path).convert("RGB").resize((width, height))
    image = pipe(
        prompt,
        image=init,
        strength=float(strength),
        height=height,
        width=width,
        num_inference_steps=family["img2img_steps"],
        guidance_scale=family["guidance"],
        **call,
    ).images[0]
else:
    print(f"loading {pipeline_model}...", file=sys.stderr)
    pipe = Pipe.from_pretrained(pipeline_model, torch_dtype=dtype, **pipeline_kwargs)
    pipe.to(device)
    image = pipe(
        prompt,
        height=height,
        width=width,
        num_inference_steps=family["steps"],
        guidance_scale=family["guidance"],
        **call,
    ).images[0]

image.save(out_path)
print(out_path)
"""
)


def resolve_image_model(model: str | None) -> tuple[str | None, str | None]:
    """Validate an optional family name, Hub pipeline ID, or GGUF text-encoder URL."""
    if not model or model == "z-image":
        return None, None
    if model in IMAGE_FAMILIES:
        return model, None
    parsed = urlparse(model)
    if not parsed.scheme:
        if model.count("/") == 1 and all(model.split("/")):
            return model, None
        known = ", ".join(IMAGE_FAMILIES)
        return None, (
            "n0b ai image: --model must be "
            f"{known}, a Hugging Face repository ID (owner/repo), "
            "or a Hugging Face GGUF file URL"
        )
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or parsed.netloc != "huggingface.co"
        or len(parts) < 5
        or parts[2] not in {"blob", "resolve"}
    ):
        return None, "n0b ai image: --model URL must name a Hugging Face file"
    if not parts[-1].lower().endswith(".gguf"):
        return None, "n0b ai image: --model file URL must end in .gguf"
    return model, None


def read_image_prompt(parts: list[str]) -> list[str]:
    """Replace each conventional '-' prompt argument with standard input."""
    return [sys.stdin.read() if part == "-" else part for part in parts]


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
    model, model_err = resolve_image_model(model)
    if model_err:
        print(model_err, file=sys.stderr)
        return 1
    if refs and model in IMAGE_FAMILIES and not IMAGE_FAMILIES[model]["img2img"]:
        print(
            f"n0b ai image: --ref is not supported for {model}",
            file=sys.stderr,
        )
        return 2
    parts = read_image_prompt(list(prompt))
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
    prefix = model if model in IMAGE_FAMILIES else "z-image"
    out_path = (
        Path(out).expanduser()
        if out
        else Path(f"{prefix}-{datetime.now():%Y-%m-%d-%H-%M-%S}.png")
    )
    try:
        python = ensure_image()
    except subprocess.CalledProcessError as exc:
        print(f"n0b ai image: setup failed: {exc}", file=sys.stderr)
        return 1
    env = os.environ.copy()
    env["PYTHONPATH"] = str(N0B_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [
            str(python),
            "-c",
            _IMAGE_SNIPPET,
            prompt_text,
            str(out_path),
            ref_path or "",
            str(strength),
            str(gen_width),
            str(gen_height),
            model or "",
        ],
        env=env,
    )
    return proc.returncode if proc.returncode is not None else 0
