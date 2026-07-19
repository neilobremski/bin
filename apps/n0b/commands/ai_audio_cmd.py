"""n0b ai audio — AudioLDM and Suno Bark."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ai_venv import ensure_audio
from commands.gpu_cmd import mps_available

_AUDIO_MODELS = {"audioldm", "bark", "suno-bark"}

_AUDIOLDM_SNIPPET = """\
import sys
from pathlib import Path

prompt, out_path, extra = sys.argv[1:4]
extra_args = extra.split("\\0") if extra else []

import torch

if not torch.cuda.is_available():
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    _orig_tensor_cuda = torch.Tensor.cuda
    _orig_module_cuda = torch.nn.Module.cuda

    def _to_device(self, _device=None):
        return self.to(device)

    torch.Tensor.cuda = _to_device
    torch.nn.Module.cuda = _to_device

from audioldm import text_to_audio, build_model

model = build_model(model_name="audioldm-s-full")
waveform = text_to_audio(
    model,
    prompt,
    seed=42,
    ddim_steps=200,
    duration=10.0,
)
import scipy.io.wavfile as wavfile
import numpy as np

sample = waveform[0]
if hasattr(sample, "detach"):
    sample = sample.detach().cpu().numpy()
else:
    sample = np.asarray(sample)
if sample.ndim == 2 and sample.shape[0] <= sample.shape[1]:
    sample = sample.T
audio = np.squeeze(sample).astype(np.float32)
audio = np.clip(audio, -1.0, 1.0)
audio_int = (audio * 32767).astype(np.int16)

out = Path(out_path)
out.parent.mkdir(parents=True, exist_ok=True)
wavfile.write(str(out), 16000, audio_int)
print(out)
"""

_BARK_SNIPPET = """\
import sys
from pathlib import Path

prompt, out_path = sys.argv[1:3]
from bark import SAMPLE_RATE, generate_audio, preload_models
import numpy as np
import scipy.io.wavfile as wavfile

preload_models()
audio = generate_audio(prompt)
out = Path(out_path)
out.parent.mkdir(parents=True, exist_ok=True)
wavfile.write(str(out), SAMPLE_RATE, audio)
print(out)
"""


@dataclass
class AudioRequest:
    model: str = "audioldm"
    prompt: str = ""
    output_file: str = ""
    extra_args: list[str] | None = None
    install: bool = False
    uninstall: bool = False


def parse_audio_args(model: str | None, argv: list[str]) -> AudioRequest:
    args = list(argv)
    if args[:1] == ["--"]:
        args = args[1:]
    chosen = (model or "audioldm").lower()
    if chosen == "suno-bark":
        chosen = "bark"
    if chosen not in _AUDIO_MODELS - {"suno-bark"}:
        raise ValueError(
            f"n0b ai audio: unknown model {model!r} (known: audioldm, bark)"
        )
    req = AudioRequest(model=chosen)
    skip = False
    for i, arg in enumerate(args):
        if skip:
            skip = False
            continue
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if arg in ("--install",):
            req.install = True
            continue
        if arg in ("--uninstall",):
            req.uninstall = True
            continue
        if arg in ("-o", "--out", "--output"):
            if not nxt:
                raise ValueError(f"n0b ai audio: {arg} requires a filename")
            req.output_file = nxt
            skip = True
            continue
        if arg.startswith("-"):
            req.extra_args = (req.extra_args or []) + [arg]
            if nxt and not nxt.startswith("-"):
                req.extra_args.append(nxt)
                skip = True
            continue
        req.prompt = arg if not req.prompt else f"{req.prompt} {arg}"
    if not req.output_file:
        stamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        ext = ".wav"
        req.output_file = f"{req.model}-{stamp}{ext}"
    return req


def cmd_audio(model: str | None, argv: list[str]) -> int:
    try:
        req = parse_audio_args(model, argv)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if req.uninstall:
        from ai_venv import uninstall as ai_venv_uninstall
        return ai_venv_uninstall()
    if req.install:
        try:
            ensure_audio(req.model)
        except subprocess.CalledProcessError as exc:
            print(f"n0b ai audio: setup failed: {exc}", file=sys.stderr)
            return 1
        return 0
    if not req.prompt:
        print("n0b ai audio: prompt required", file=sys.stderr)
        return 2
    try:
        python = ensure_audio(req.model)
    except subprocess.CalledProcessError as exc:
        print(f"n0b ai audio: setup failed: {exc}", file=sys.stderr)
        return 1
    out_path = Path(req.output_file).expanduser()
    env = None
    if req.model == "bark" and mps_available():
        import os
        env = os.environ.copy()
        env["SUNO_ENABLE_MPS"] = "True"
        print("MPS available — SUNO_ENABLE_MPS=True", file=sys.stderr)
    if req.model == "bark":
        snippet = _BARK_SNIPPET
        cmd = [str(python), "-c", snippet, req.prompt, str(out_path)]
    else:
        extra = "\\0".join(req.extra_args or [])
        snippet = _AUDIOLDM_SNIPPET
        cmd = [str(python), "-c", snippet, req.prompt, str(out_path), extra]
    print(f"Generating audio: {req.prompt}", file=sys.stderr)
    proc = subprocess.run(cmd, env=env)
    if proc.returncode != 0:
        return proc.returncode
    print(out_path)
    return 0
