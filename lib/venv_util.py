"""Shared, isolated Python dependency groups for every app in this repo."""
from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_VENV = REPO_ROOT / ".venv"
REQUIREMENTS_DIR = REPO_ROOT / "requirements"

LEGACY_VENVS = (
    REPO_ROOT / "venv",
    REPO_ROOT / "apps" / "b3t" / ".venv",
    Path.home() / ".cache" / "n0b" / "kokoro-venv",
    Path.home() / ".cache" / "n0b" / "whisper-venv",
    Path.home() / ".cache" / "n0b" / "z-image-venv",
)
ZIMAGE_LEGACY_REPO = Path.home() / "repos" / "Z-Image"

AI_VENV = BIN_VENV
_ORIGINAL_PYTHONPATH = os.environ.get("PYTHONPATH", "")
_GROUP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _base_python() -> str:
    uv = shutil.which("uv")
    if uv:
        found = subprocess.run(
            [uv, "python", "find", "3.12"],
            capture_output=True,
            text=True,
        )
        if found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    for name in ("python3.12", "python3.11", "python3.13"):
        found = shutil.which(name)
        if found:
            return found
    if sys.executable:
        return sys.executable
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return found
    raise FileNotFoundError("no Python interpreter found")


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python3"


def runtime_venv() -> Path:
    return BIN_VENV / "runtime"


def python_bin() -> Path:
    return _venv_python(runtime_venv())


def requirements_file(name: str) -> Path:
    path = REQUIREMENTS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"missing requirements file: {path}")
    return path


def _python_works(python: Path) -> bool:
    if not python.is_file():
        return False
    return subprocess.run(
        [str(python), "-c", ""], capture_output=True
    ).returncode == 0


def _replace_runtime(base_python: str) -> Path:
    BIN_VENV.mkdir(parents=True, exist_ok=True)
    temp_venv = BIN_VENV / f".runtime-{uuid.uuid4().hex}.tmp"
    old_venv = None
    try:
        subprocess.run(
            [base_python, "-m", "venv", str(temp_venv)],
            check=True,
            stdout=sys.stderr,
        )
        candidate = _venv_python(temp_venv)
        if not _python_works(candidate):
            raise subprocess.CalledProcessError(1, [str(candidate), "-c", ""])
        current = runtime_venv()
        if current.exists():
            old_venv = BIN_VENV / f".runtime-{uuid.uuid4().hex}.old"
            os.replace(current, old_venv)
        try:
            os.replace(temp_venv, current)
        except OSError:
            if old_venv is not None:
                os.replace(old_venv, current)
            raise
    finally:
        if temp_venv.exists():
            shutil.rmtree(temp_venv, ignore_errors=True)
        if old_venv is not None and old_venv.exists():
            shutil.rmtree(old_venv, ignore_errors=True)
    return python_bin()


def _migrate_legacy_venv(base_python: str) -> Path:
    old_root = BIN_VENV.parent / f".{BIN_VENV.name}-{uuid.uuid4().hex}.old"
    os.replace(BIN_VENV, old_root)
    try:
        python = _replace_runtime(base_python)
    except BaseException:
        shutil.rmtree(BIN_VENV, ignore_errors=True)
        os.replace(old_root, BIN_VENV)
        raise
    shutil.rmtree(old_root, ignore_errors=True)
    return python


def _ensure_venv() -> Path:
    python = python_bin()
    base_python = _base_python()
    if _python_works(python) and interpreter_key(python) == interpreter_key(
        Path(base_python)
    ):
        return python
    print(f"Setting up shared Python runtime at {BIN_VENV}...", file=sys.stderr)
    if (BIN_VENV / "pyvenv.cfg").is_file():
        return _migrate_legacy_venv(base_python)
    return _replace_runtime(base_python)


def interpreter_key(python: Path | None = None) -> str:
    python = python or _ensure_venv()
    script = (
        "import sys,sysconfig; "
        "print(f'{sys.implementation.cache_tag}-{sysconfig.get_platform()}')"
    )
    proc = subprocess.run(
        [str(python), "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def group_dir(group: str, python: Path | None = None) -> Path:
    if not _GROUP_RE.fullmatch(group):
        raise ValueError(f"invalid dependency group: {group!r}")
    return BIN_VENV / "groups" / interpreter_key(python) / group


def _torch_requirements() -> Path:
    name = "ai-torch-cuda.txt" if shutil.which("nvidia-smi") else "ai-torch-cpu.txt"
    return requirements_file(name)


def _group_requirements(group: str) -> list[Path]:
    if group == "ai":
        return [_torch_requirements(), requirements_file("ai.txt")]
    if group == "ai-mlx":
        return [requirements_file("ai-mlx.txt")]
    return [requirements_file(f"{group}.txt")]


def _requirements_digest(req_files: list[Path]) -> str:
    digest = hashlib.sha256()
    for req_file in req_files:
        digest.update(req_file.name.encode())
        digest.update(b"\0")
        digest.update(req_file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _group_env(directory: Path) -> dict[str, str]:
    env = os.environ.copy()
    paths = [str(directory)]
    if _ORIGINAL_PYTHONPATH:
        paths.append(_ORIGINAL_PYTHONPATH)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _probe(python: Path, directory: Path, cmd: str) -> bool:
    return subprocess.run(
        [str(python), "-c", cmd],
        env=_group_env(directory),
        capture_output=True,
    ).returncode == 0


def _group_ready(
    python: Path,
    directory: Path,
    digest: str,
    probe: str | None,
) -> bool:
    marker = directory / ".requirements.sha256"
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != digest:
        return False
    return probe is None or _probe(python, directory, probe)


def _install_group(
    python: Path,
    group: str,
    directory: Path,
    req_files: list[Path],
    digest: str,
) -> None:
    directory.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{group}-", suffix=".tmp", dir=directory.parent
        )
    )
    installer = shutil.which("uv")
    argv = (
        [
            installer,
            "pip",
            "install",
            "--python",
            str(python),
            "--target",
            str(temp_dir),
        ]
        if installer
        else [str(python), "-m", "pip", "install", "--target", str(temp_dir)]
    )
    for req_file in req_files:
        argv.extend(("-r", str(req_file)))
    old_dir = None
    try:
        subprocess.run(argv, check=True, stdout=sys.stderr)
        (temp_dir / ".requirements.sha256").write_text(
            digest + "\n", encoding="utf-8"
        )
        if directory.exists():
            old_dir = directory.parent / f".{group}-{uuid.uuid4().hex}.old"
            os.replace(directory, old_dir)
        try:
            os.replace(temp_dir, directory)
        except OSError:
            if old_dir is not None:
                os.replace(old_dir, directory)
            raise
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        if old_dir is not None and old_dir.exists():
            shutil.rmtree(old_dir, ignore_errors=True)


def _activate_group(directory: Path) -> None:
    env = _group_env(directory)
    os.environ["PYTHONPATH"] = env["PYTHONPATH"]
    os.environ["PYTHONNOUSERSITE"] = "1"


def ensure_group(group: str, probe: str | None = None) -> Path:
    python = _ensure_venv()
    req_files = _group_requirements(group)
    digest = _requirements_digest(req_files)
    directory = group_dir(group, python)
    if not _group_ready(python, directory, digest, probe):
        print(f"Installing {group} dependencies into {directory}...", file=sys.stderr)
        _install_group(python, group, directory, req_files, digest)
        if probe and not _probe(python, directory, probe):
            raise subprocess.CalledProcessError(1, [str(python), "-c", probe])
    _activate_group(directory)
    return python


def ensure_image() -> Path:
    probe = (
        "import gguf, torch; "
        "from diffusers import ErnieImagePipeline, ZImagePipeline, ZImageImg2ImgPipeline"
    )
    return ensure_group("ai", probe=probe)


def ensure_kokoro() -> Path:
    return ensure_group("ai", probe="import kokoro, soundfile")


def ensure_whisper() -> Path:
    return ensure_group("ai", probe="import whisper")


def ensure_mlx_whisper() -> Path:
    return ensure_group("ai-mlx", probe="import mlx_whisper")


def ensure_parakeet() -> Path:
    return ensure_group("ai-mlx", probe="import parakeet_mlx")


def ensure_dev() -> Path:
    return ensure_group("dev", probe="import pytest")


def ensure_b3t() -> Path:
    return ensure_group("b3t", probe="import openpyxl, requests")


def ensure_audio(model: str = "audioldm") -> Path:
    model = (model or "audioldm").lower()
    if model in ("bark", "suno-bark"):
        return ensure_group("audio", probe="import bark")
    return ensure_group("audio", probe="import audioldm")


def install_all() -> Path:
    for group in ("ai", "dev", "b3t", "audio"):
        ensure_group(group)
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        ensure_group("ai-mlx")
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
