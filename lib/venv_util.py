"""Shared ~/.bin/.venv management for all apps in this repo."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_VENV = REPO_ROOT / ".venv"
REQUIREMENTS_DIR = REPO_ROOT / "requirements"

LEGACY_VENVS = (
    REPO_ROOT / "venv",
    Path.home() / ".cache" / "n0b" / "kokoro-venv",
    Path.home() / ".cache" / "n0b" / "whisper-venv",
    Path.home() / ".cache" / "n0b" / "z-image-venv",
)
ZIMAGE_LEGACY_REPO = Path.home() / "repos" / "Z-Image"

AI_VENV = BIN_VENV


def _base_python() -> str:
    for name in ("python3.13", "python3.12", "python3.11"):
        if shutil.which(name):
            return name
    return sys.executable


def python_bin() -> Path:
    return BIN_VENV / "bin" / "python3"


def requirements_file(name: str) -> Path:
    path = REQUIREMENTS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"missing requirements file: {path}")
    return path


def _probe(cmd: str) -> bool:
    py = python_bin()
    if not py.is_file():
        return False
    return subprocess.run([str(py), "-c", cmd], capture_output=True).returncode == 0


def _ensure_venv() -> Path:
    py = python_bin()
    if py.is_file():
        return py
    print(f"Setting up venv at {BIN_VENV} (one-time)...", file=sys.stderr)
    BIN_VENV.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [_base_python(), "-m", "venv", str(BIN_VENV)],
        check=True,
        stdout=sys.stderr,
    )
    return py


def _pip_upgrade() -> Path:
    py = _ensure_venv()
    subprocess.run(
        [str(py), "-m", "pip", "install", "--upgrade", "pip"],
        check=True,
        stdout=sys.stderr,
    )
    return py


def _pip_install_file(req_file: Path) -> Path:
    py = _pip_upgrade()
    subprocess.run(
        [str(py), "-m", "pip", "install", "-r", str(req_file)],
        check=True,
        stdout=sys.stderr,
    )
    return py


def _torch_requirements() -> Path:
    name = "ai-torch-cuda.txt" if shutil.which("nvidia-smi") else "ai-torch-cpu.txt"
    return requirements_file(name)


def _group_requirements(group: str) -> list[Path]:
    if group == "ai":
        return [_torch_requirements(), requirements_file("ai.txt")]
    return [requirements_file(f"{group}.txt")]


def ensure_group(group: str, probe: str | None = None) -> Path:
    if probe and _probe(probe):
        return python_bin()
    for req_file in _group_requirements(group):
        _pip_install_file(req_file)
    return python_bin()


def ensure_image() -> Path:
    probe = (
        "import torch; from diffusers import ZImagePipeline, ZImageImg2ImgPipeline"
    )
    return ensure_group("ai", probe=probe)


def ensure_kokoro() -> Path:
    return ensure_group("ai", probe="import kokoro, soundfile")


def ensure_whisper() -> Path:
    return ensure_group("ai", probe="import whisper")


def ensure_dev() -> Path:
    return ensure_group("dev", probe="import pytest")


def ensure_b3t() -> Path:
    return ensure_group("b3t", probe="import openpyxl, requests")


def ensure_a8s_test() -> Path:
    return ensure_group("a8s-test", probe="import paho.mqtt")


def ensure_r4t() -> Path:
    return ensure_group("r4t", probe="import textual")


def ensure_audio(model: str = "audioldm") -> Path:
    model = (model or "audioldm").lower()
    if model in ("bark", "suno-bark"):
        return ensure_group("audio", probe="import bark")
    return ensure_group("audio", probe="import audioldm")


def install_all() -> Path:
    for group in ("ai", "dev", "b3t", "a8s-test", "r4t", "audio"):
        ensure_group(group)
    return python_bin()


def uninstall() -> int:
    removed: list[str] = []
    for path in (BIN_VENV, *LEGACY_VENVS, ZIMAGE_LEGACY_REPO):
        if path.exists():
            shutil.rmtree(path)
            removed.append(str(path))
    if removed:
        print(f"removed: {', '.join(removed)}", file=sys.stderr)
    else:
        print("bin: nothing to uninstall", file=sys.stderr)
    return 0
