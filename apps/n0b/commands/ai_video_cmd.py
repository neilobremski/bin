"""n0b ai video — LTX-Video 1/2 and MLX-Video backends."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from commands.gpu_cmd import mlx_available

_VIDEO_MODEL_FLAGS = {
    "ltx-2": ["--ltx2"],
    "ltx2": ["--ltx2"],
    "ltx-1": ["--ltx1"],
    "ltx1": ["--ltx1"],
}


def _find_repo(name: str) -> Path | None:
    for base in (Path.home() / "repos", Path.home()):
        path = base / name
        if path.is_dir():
            return path
    return None


def _has_arg(args: list[str], prefix: str) -> bool:
    return any(a == prefix or a.startswith(f"{prefix}=") for a in args)


@dataclass
class VideoRequest:
    ltx_version: int = 0
    model: str = ""
    output_file: str = ""
    prompt: str = ""
    negative_prompt: str = ""
    reference_images: list[str] = field(default_factory=list)
    inference_args: list[str] = field(default_factory=list)
    install: bool = False
    install_ltx1: bool = False


def parse_video_args(argv: list[str]) -> VideoRequest:
    args = list(argv)
    if args[:1] == ["--"]:
        args = args[1:]
    req = VideoRequest()
    output_idx = -1
    for idx in range(len(args) - 1, -1, -1):
        val = args[idx]
        if not val.endswith(".mp4"):
            continue
        prev = args[idx - 1] if idx > 0 else ""
        if prev in ("--output-path", "--output_path"):
            continue
        req.output_file = val
        output_idx = idx
        break
    if not req.output_file:
        req.output_file = f"{datetime.now():%Y-%m-%d-%H%M%S}-ltx-video.mp4"

    i = 0
    while i < len(args):
        if i == output_idx:
            i += 1
            continue
        arg = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else ""

        if arg == "--install":
            req.install = True
            i += 1
            continue
        if arg == "--install-ltx1":
            req.install_ltx1 = True
            i += 1
            continue
        if arg in ("--ltx1", "-1"):
            req.ltx_version = 1
            i += 1
            continue
        if arg in ("--ltx2", "-2"):
            req.ltx_version = 2
            i += 1
            continue
        if arg in ("--model", "-m"):
            if not nxt:
                raise ValueError(f"n0b ai video: {arg} requires a model name")
            req.model = nxt
            i += 2
            continue

        if arg.startswith("-"):
            if (
                arg.startswith("-")
                and not arg.startswith("--")
                and len(arg) > 1
                and not arg[1:].isdigit()
            ):
                text = arg[1:]
                req.negative_prompt = (
                    text if not req.negative_prompt else f"{req.negative_prompt}, {text}"
                )
                i += 1
                continue
            if "=" in arg:
                req.inference_args.append(arg)
                i += 1
                continue
            is_value = bool(nxt) and not nxt.startswith("-") and (i + 1) != output_idx
            if is_value:
                req.inference_args.extend([arg, nxt])
                i += 2
            else:
                req.inference_args.append(arg)
                i += 1
            continue

        if Path(arg).expanduser().is_file():
            req.reference_images.append(str(Path(arg).expanduser()))
            i += 1
            continue

        req.prompt = arg if not req.prompt else f"{req.prompt} {arg}"
        i += 1
    return req


def install_ltx2() -> int:
    install_dir = Path.home() / "repos" if (Path.home() / "repos").is_dir() else Path.home()
    target = install_dir / "LTX-2"
    if target.is_dir():
        print(f"LTX-2 repo already exists at {target}", file=sys.stderr)
        subprocess.run(["git", "pull"], cwd=target, check=True)
    else:
        print(f"Cloning LTX-2 to {target}...", file=sys.stderr)
        subprocess.run(
            ["git", "clone", "https://github.com/Lightricks/LTX-2.git", str(target)],
            check=True,
        )
    if not shutil.which("uv"):
        if shutil.which("brew"):
            subprocess.run(["brew", "install", "uv"], check=True)
        else:
            print("uv not found; install from https://github.com/astral-sh/uv", file=sys.stderr)
            return 1
    print("Syncing LTX-2 virtual environment with uv...", file=sys.stderr)
    subprocess.run(["uv", "sync"], cwd=target, check=True, stdout=sys.stderr)
    print(f"LTX-2 installed at {target}", file=sys.stderr)
    print(f"Download models: python {target / 'download_models.py'}", file=sys.stderr)
    return 0


def install_ltx1() -> int:
    install_dir = Path.home() / "repos" if (Path.home() / "repos").is_dir() else Path.home()
    target = install_dir / "LTX-Video"
    if target.is_dir():
        print(f"LTX-Video repo already exists at {target}", file=sys.stderr)
        subprocess.run(["git", "pull"], cwd=target, check=True)
    else:
        print(f"Cloning LTX-Video to {target}...", file=sys.stderr)
        subprocess.run(
            ["git", "clone", "https://github.com/Lightricks/LTX-Video.git", str(target)],
            check=True,
        )
    venv_py = target / "venv" / "bin" / "python3"
    if not venv_py.is_file():
        subprocess.run([sys.executable, "-m", "venv", str(target / "venv")], check=True)
    subprocess.run([str(venv_py), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run(
        [str(venv_py), "-m", "pip", "install", "-e", ".[inference]"],
        cwd=target,
        check=True,
        stdout=sys.stderr,
    )
    torch = ["torch", "torchvision", "torchaudio"]
    if shutil.which("nvidia-smi"):
        subprocess.run(
            [str(venv_py), "-m", "pip", "install", *torch,
             "--index-url", "https://download.pytorch.org/whl/cu118"],
            check=True,
            stdout=sys.stderr,
        )
    else:
        subprocess.run([str(venv_py), "-m", "pip", "install", *torch], check=True, stdout=sys.stderr)
    print(f"LTX-Video 1 installed at {target}", file=sys.stderr)
    return 0


def _mlx_compat() -> bool:
    if not mlx_available():
        return False
    return _find_repo("mlx-video") is not None


def _resolve_version(req: VideoRequest, use_mlx: bool, ltx2_dir: Path | None) -> int:
    if req.ltx_version:
        return req.ltx_version
    if req.model:
        m = req.model.lower()
        if "ltx-2" in m or "ltx2" in m or "2.3" in m or m in ("distilled", "dev"):
            return 2
        return 1
    if ltx2_dir or use_mlx:
        return 2
    return 1


def _defaults(version: int, model: str) -> tuple[str, int, int, int, int]:
    if version == 2:
        return model or "ltx-2.3-22b-distilled-1.1", 97, 24, 512, 832
    return model or "ltxv-2b-0.9.8-distilled", 121, 24, 480, 832


def _run(cmd: list[str], cwd: Path | None = None) -> int:
    print(f"Running: {' '.join(cmd)}", file=sys.stderr)
    return subprocess.run(cmd, cwd=cwd).returncode or 0


def _finalize_output(temp_dir: Path, output_file: str, start: float) -> int:
    generated = temp_dir / "output.mp4"
    if not generated.is_file():
        found = next(temp_dir.rglob("*.mp4"), None)
        generated = found if found else generated
    if not generated.is_file():
        print(f"n0b ai video: no video generated in {temp_dir}", file=sys.stderr)
        return 1
    shutil.move(str(generated), output_file)
    elapsed = int(time.time() - start)
    print(f"Video saved to: {output_file}", file=sys.stderr)
    print(f"Generation time: {elapsed} seconds", file=sys.stderr)
    return 0


def _run_ltx1(req: VideoRequest, ltx_dir: Path, temp_dir: Path) -> int:
    model, num_frames, frame_rate, height, width = _defaults(1, req.model)
    py = ltx_dir / "venv" / "bin" / "python3"
    if not py.is_file():
        py = Path(sys.executable)
    cmd = [
        str(py),
        str(ltx_dir / "inference.py"),
        "--pipeline_config",
        str(ltx_dir / "configs" / f"{model}.yaml"),
        "--output_path",
        str(temp_dir),
    ]
    if not _has_arg(req.inference_args, "--height"):
        cmd.extend(["--height", str(height)])
    if not _has_arg(req.inference_args, "--width"):
        cmd.extend(["--width", str(width)])
    if not _has_arg(req.inference_args, "--num_frames"):
        cmd.extend(["--num_frames", str(num_frames)])
    if not _has_arg(req.inference_args, "--frame_rate"):
        cmd.extend(["--frame_rate", str(frame_rate)])
    if req.prompt:
        cmd.extend(["--prompt", req.prompt])
    if req.negative_prompt:
        cmd.extend(["--negative_prompt", req.negative_prompt])
    if req.reference_images:
        n = len(req.reference_images)
        positions = [0] if n == 1 else [
            int(i * (num_frames - 1) / (n - 1)) for i in range(n)
        ]
        cmd.append("--conditioning_media_paths")
        cmd.extend(req.reference_images)
        cmd.append("--conditioning_start_frames")
        cmd.extend(str(p) for p in positions)
    cmd.extend(req.inference_args)
    start = time.time()
    rc = _run(cmd)
    if rc != 0:
        return rc
    return _finalize_output(temp_dir, req.output_file, start)


def _run_mlx(req: VideoRequest, temp_dir: Path) -> int:
    mlx_dir = _find_repo("mlx-video")
    if mlx_dir is None:
        print("n0b ai video: mlx-video repo not found", file=sys.stderr)
        return 1
    py = mlx_dir / ".venv" / "bin" / "python3"
    if not py.is_file():
        print(
            f"n0b ai video: MLX venv missing at {mlx_dir / '.venv'} — "
            f"run: cd {mlx_dir} && uv venv && uv pip install -e .",
            file=sys.stderr,
        )
        return 1
    model, num_frames, frame_rate, height, width = _defaults(2, req.model)
    m = model.lower()
    if m in ("ltx-2.3-22b-distilled-1.1", "distilled", "ltx2-distilled"):
        local = Path.home() / "models/LTX-2/mlx/distilled"
        model_repo = str(local) if local.is_dir() else "prince-canuma/LTX-2.3-distilled"
        pipeline = "distilled"
    elif m in ("ltx-2.3-22b-dev", "dev", "ltx2-dev"):
        local = Path.home() / "models/LTX-2/mlx/dev"
        model_repo = str(local) if local.is_dir() else "prince-canuma/LTX-2.3-dev"
        pipeline = "dev"
    else:
        model_repo = model
        pipeline = "distilled"
    cmd = [
        str(py),
        "-m",
        "mlx_video.models.ltx_2.generate",
        "--model-repo",
        model_repo,
        "--pipeline",
        pipeline,
    ]
    ia = req.inference_args
    if not _has_arg(ia, "--height") and not _has_arg(ia, "-H"):
        cmd.extend(["--height", str(height)])
    if not _has_arg(ia, "--width") and not _has_arg(ia, "-W"):
        cmd.extend(["--width", str(width)])
    if not _has_arg(ia, "--num-frames") and not _has_arg(ia, "-n"):
        cmd.extend(["--num-frames", str(num_frames)])
    if not _has_arg(ia, "--fps"):
        cmd.extend(["--fps", str(frame_rate)])
    if not _has_arg(ia, "--output-path") and not _has_arg(ia, "-o"):
        cmd.extend(["--output-path", str(temp_dir / "output.mp4")])
    if req.prompt and not _has_arg(ia, "--prompt") and not _has_arg(ia, "-p"):
        cmd.extend(["--prompt", req.prompt])
    if req.reference_images and not _has_arg(ia, "--image") and not _has_arg(ia, "-i"):
        cmd.extend(["--image", req.reference_images[0]])
        if len(req.reference_images) > 1 and not _has_arg(ia, "--end-image"):
            cmd.extend(["--end-image", req.reference_images[-1]])
        if len(req.reference_images) > 2:
            print(
                "n0b ai video: MLX supports at most 2 reference images; using first and last",
                file=sys.stderr,
            )
    cmd.extend(ia)
    start = time.time()
    rc = _run(cmd)
    if rc != 0:
        return rc
    return _finalize_output(temp_dir, req.output_file, start)


def _run_ltx2_pytorch(req: VideoRequest, ltx_dir: Path, temp_dir: Path) -> int:
    py = ltx_dir / ".venv" / "bin" / "python3"
    if not py.is_file():
        print("n0b ai video: LTX-2 venv missing — run: n0b ai video --install", file=sys.stderr)
        return 1
    model, num_frames, frame_rate, height, width = _defaults(2, req.model)
    m = model.lower()
    distilled_lora = ""
    if m in ("ltx-2.3-22b-distilled-1.1", "distilled", "ltx2-distilled"):
        pipeline = "ltx_pipelines.distilled"
        ck_flag = "--distilled-checkpoint-path"
        ck_path = Path.home() / "models/LTX-2/checkpoint/ltx-2.3-22b-distilled-1.1.safetensors"
    elif m in ("ltx-2.3-22b-dev", "dev", "ltx2-dev"):
        pipeline = "ltx_pipelines.ti2vid_two_stages"
        ck_flag = "--checkpoint-path"
        ck_path = Path.home() / "models/LTX-2/checkpoint/ltx-2.3-22b-dev.safetensors"
        distilled_lora = str(
            Path.home() / "models/LTX-2/lora/ltx-2.3-22b-distilled-lora-384-1.1.safetensors"
        )
    else:
        pipeline = "ltx_pipelines.ti2vid_two_stages"
        ck_flag = ""
        ck_path = Path()
    gemma = Path.home() / "models/LTX-2/gemma"
    upsampler = Path.home() / "models/LTX-2/upscaler/ltx-2.3-spatial-upscaler-x2-1.1.safetensors"
    if ck_flag and not ck_path.is_file():
        print(f"n0b ai video: checkpoint missing: {ck_path}", file=sys.stderr)
        print(f"  python {ltx_dir / 'download_models.py'}", file=sys.stderr)
        return 1
    if ck_flag and not gemma.is_dir():
        print(f"n0b ai video: gemma encoder missing: {gemma}", file=sys.stderr)
        return 1
    if ck_flag and not upsampler.is_file():
        print(f"n0b ai video: upsampler missing: {upsampler}", file=sys.stderr)
        return 1
    cmd = [str(py), "-m", pipeline]
    ia = req.inference_args
    if ck_flag and not _has_arg(ia, "--checkpoint-path") and not _has_arg(
        ia, "--distilled-checkpoint-path"
    ):
        cmd.extend([ck_flag, str(ck_path)])
    if not _has_arg(ia, "--gemma-root"):
        cmd.extend(["--gemma-root", str(gemma)])
    if not _has_arg(ia, "--spatial-upsampler-path"):
        cmd.extend(["--spatial-upsampler-path", str(upsampler)])
    if pipeline == "ltx_pipelines.ti2vid_two_stages" and distilled_lora:
        if not _has_arg(ia, "--distilled-lora"):
            if not Path(distilled_lora).is_file():
                print(f"n0b ai video: distilled LoRA missing: {distilled_lora}", file=sys.stderr)
                return 1
            cmd.extend(["--distilled-lora", distilled_lora, "0.8"])
    if not _has_arg(ia, "--height"):
        cmd.extend(["--height", str(height)])
    if not _has_arg(ia, "--width"):
        cmd.extend(["--width", str(width)])
    if not _has_arg(ia, "--num-frames"):
        cmd.extend(["--num-frames", str(num_frames)])
    if not _has_arg(ia, "--frame-rate"):
        cmd.extend(["--frame-rate", str(frame_rate)])
    cmd.extend(["--output-path", str(temp_dir / "output.mp4")])
    if req.prompt:
        cmd.extend(["--prompt", req.prompt])
    if req.negative_prompt and pipeline != "ltx_pipelines.distilled":
        if not _has_arg(ia, "--negative-prompt"):
            cmd.extend(["--negative-prompt", req.negative_prompt])
    if req.reference_images and not _has_arg(ia, "--image"):
        n = len(req.reference_images)
        for i, ref in enumerate(req.reference_images):
            pos = 0 if n == 1 else int(i * (num_frames - 1) / (n - 1))
            cmd.extend(["--image", ref, str(pos), "1.0"])
    cmd.extend(ia)
    start = time.time()
    rc = _run(cmd, cwd=ltx_dir)
    if rc != 0:
        return rc
    return _finalize_output(temp_dir, req.output_file, start)


def cmd_video(model: str | None, argv: list[str]) -> int:
    extra = _VIDEO_MODEL_FLAGS.get(model or "", [])
    if model in ("ltx-video", "ltx-2", "ltx2", "ltx-1", "ltx1", None) and not extra:
        extra = []
    try:
        req = parse_video_args([*extra, *argv])
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if req.install:
        return install_ltx2()
    if req.install_ltx1:
        return install_ltx1()

    use_mlx = _mlx_compat()
    ltx2_dir = _find_repo("LTX-2")
    ltx1_dir = _find_repo("LTX-Video")
    version = _resolve_version(req, use_mlx, ltx2_dir)

    if version == 2:
        if not use_mlx and ltx2_dir is None:
            print("n0b ai video: LTX-2 not found — run: n0b ai video --install", file=sys.stderr)
            return 1
        ltx_dir = ltx2_dir
    else:
        if ltx1_dir is None:
            print("n0b ai video: LTX-Video 1 not found — run: n0b ai video --install-ltx1", file=sys.stderr)
            return 1
        ltx_dir = ltx1_dir

    with tempfile.TemporaryDirectory() as tmp:
        temp_dir = Path(tmp)
        print(f"Temporary output directory: {temp_dir}", file=sys.stderr)
        if version == 1:
            return _run_ltx1(req, ltx_dir, temp_dir)
        if use_mlx:
            return _run_mlx(req, temp_dir)
        return _run_ltx2_pytorch(req, ltx_dir, temp_dir)
