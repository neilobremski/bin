"""Video utilities."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

MAX_FRAMES_THUMB = 50
MAX_FPS_THUMB = 1.0
FRAME_SCALE_THUMB = 512

PRESETS = {
    "thumb": {
        "width": 320,
        "colors": 64,
        "palette_fps": 2.0,
        "palette_scale": 320,
        "adaptive": True,
        "max_frames": MAX_FRAMES_THUMB,
        "max_fps": MAX_FPS_THUMB,
        "fps": None,
        "gif_scale_from_frames": True,
    },
    "small": {
        "width": 800,
        "colors": 32,
        "palette_fps": 8.0,
        "palette_scale": 640,
        "adaptive": False,
        "max_frames": None,
        "max_fps": None,
        "fps": 8.0,
        "gif_scale_from_frames": False,
    },
}


def cmd_last_frame(video: str, output: str | None) -> int:
    video_path = Path(video)
    if not video_path.is_file():
        print(f"Error: Video file not found: {video}", file=sys.stderr)
        return 1
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found on PATH", file=sys.stderr)
        return 1
    out = output or f"last-frame-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.png"
    print(f"Extracting last frame from: {video}")
    rc = subprocess.run(
        [
            "ffmpeg",
            "-sseof",
            "-1",
            "-i",
            str(video_path),
            "-update",
            "1",
            "-q:v",
            "1",
            "-frames:v",
            "1",
            out,
        ]
    ).returncode
    if rc == 0:
        print(f"Last frame saved to: {out}")
    return rc if rc is not None else 1


def _probe_duration_seconds(path: Path) -> float | None:
    if shutil.which("ffprobe") is None:
        return None
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    raw = proc.stdout.strip()
    if not raw:
        return None
    try:
        duration = float(raw)
    except ValueError:
        return None
    if duration <= 0:
        return None
    return duration


def calc_gif_fps(
    path: Path,
    *,
    max_frames: int = MAX_FRAMES_THUMB,
    max_fps: float = MAX_FPS_THUMB,
) -> float:
    duration = _probe_duration_seconds(path)
    if duration is None:
        print(
            f"n0b video gif: no duration; using max fps {max_fps}",
            file=sys.stderr,
        )
        return max_fps
    fps = max_frames / duration
    if fps > max_fps:
        return max_fps
    return round(fps, 2)


def _run_ffmpeg(argv: list[str]) -> None:
    proc = subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(detail or "ffmpeg failed")


def _create_palette(
    source: Path,
    palette_path: Path,
    *,
    fps: float,
    scale: int,
    colors: int,
) -> None:
    print(
        f"creating palette ({colors} colors, fps={fps}, scale={scale})...",
        file=sys.stderr,
    )
    _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vf",
            f"fps={fps},scale={scale}:-1:flags=lanczos,"
            f"palettegen=stats_mode=diff:max_colors={colors}",
            str(palette_path),
        ]
    )


def _extract_frames(path: Path, frames_dir: Path, fps: float, scale: int) -> list[Path]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    pattern = frames_dir / "%04d.png"
    print(f"extracting frames at {fps} FPS (scale={scale})...", file=sys.stderr)
    _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(path),
            "-vf",
            f"fps={fps},scale='min({scale},iw):-2'",
            str(pattern),
        ]
    )
    frames = sorted(frames_dir.glob("*.png"))
    if not frames:
        raise RuntimeError("no frames extracted")
    print(f"extracted {len(frames)} frame(s)", file=sys.stderr)
    return frames


def _assemble_gif_from_frames(
    frames_dir: Path,
    palette_path: Path,
    output: Path,
    *,
    fps: float,
    width: int,
) -> None:
    print(f"creating GIF ({width}px, {fps} FPS)...", file=sys.stderr)
    _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(frames_dir / "%04d.png"),
            "-i",
            str(palette_path),
            "-filter_complex",
            f"fps={fps},scale={width}:-1:flags=lanczos[x];"
            f"[x][1:v]paletteuse=dither=sierra2_4a",
            str(output),
        ]
    )


def _assemble_gif_from_video(
    video_path: Path,
    palette_path: Path,
    output: Path,
    *,
    fps: float,
    width: int,
) -> None:
    print(f"creating GIF ({width}px, {fps} FPS)...", file=sys.stderr)
    _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(palette_path),
            "-filter_complex",
            f"fps={fps},scale={width}:-1:flags=lanczos[x];"
            f"[x][1:v]paletteuse=dither=sierra2_4a",
            str(output),
        ]
    )


def resolve_gif_settings(
    preset: str,
    *,
    fps: float | None = None,
    width: int | None = None,
    colors: int | None = None,
    max_frames: int | None = None,
) -> dict:
    if preset not in PRESETS:
        raise ValueError(f"unknown preset: {preset}")
    settings = dict(PRESETS[preset])
    if fps is not None:
        settings["fps"] = fps
        settings["adaptive"] = False
    if width is not None:
        settings["width"] = width
    if colors is not None:
        settings["colors"] = colors
    if max_frames is not None:
        settings["max_frames"] = max_frames
    return settings


def cmd_gif(
    video: str,
    output: str | None = None,
    *,
    preset: str = "thumb",
    fps: float | None = None,
    width: int | None = None,
    colors: int | None = None,
    max_frames: int | None = None,
) -> int:
    video_path = Path(video).expanduser()
    if not video_path.is_file():
        print(f"n0b video gif: no such file: {video}", file=sys.stderr)
        return 1
    if not shutil.which("ffmpeg"):
        print(
            "n0b video gif: ffmpeg not found (try: brew install ffmpeg)",
            file=sys.stderr,
        )
        return 1

    try:
        settings = resolve_gif_settings(
            preset,
            fps=fps,
            width=width,
            colors=colors,
            max_frames=max_frames,
        )
    except ValueError as exc:
        print(f"n0b video gif: {exc}", file=sys.stderr)
        return 2

    out_path = Path(output).expanduser() if output else Path(f"{video_path.stem}.gif")
    gif_fps = settings["fps"]
    if settings["adaptive"]:
        gif_fps = calc_gif_fps(
            video_path,
            max_frames=int(settings["max_frames"]),
            max_fps=float(settings["max_fps"]),
        )

    print(
        f"preset: {preset} (fps={gif_fps}, width={settings['width']}, "
        f"colors={settings['colors']})",
        file=sys.stderr,
    )

    try:
        with tempfile.TemporaryDirectory(prefix="n0b-gif-") as tmp:
            tmp_dir = Path(tmp)
            palette_path = tmp_dir / "palette.png"

            if settings["gif_scale_from_frames"]:
                frames_dir = tmp_dir / "frames"
                _extract_frames(
                    video_path,
                    frames_dir,
                    float(gif_fps),
                    FRAME_SCALE_THUMB,
                )
                _create_palette(
                    video_path,
                    palette_path,
                    fps=float(settings["palette_fps"]),
                    scale=int(settings["palette_scale"]),
                    colors=int(settings["colors"]),
                )
                _assemble_gif_from_frames(
                    frames_dir,
                    palette_path,
                    out_path,
                    fps=float(gif_fps),
                    width=int(settings["width"]),
                )
            else:
                _create_palette(
                    video_path,
                    palette_path,
                    fps=float(gif_fps),
                    scale=int(settings["palette_scale"]),
                    colors=int(settings["colors"]),
                )
                _assemble_gif_from_video(
                    video_path,
                    palette_path,
                    out_path,
                    fps=float(gif_fps),
                    width=int(settings["width"]),
                )
    except RuntimeError as exc:
        print(f"n0b video gif: {exc}", file=sys.stderr)
        return 1

    print(f"GIF saved to: {out_path}")
    return 0
