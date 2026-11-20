"""Subprocess entry point for local Kyutai Pocket TTS synthesis."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

_N0B_DIR = Path(__file__).resolve().parents[1]
if str(_N0B_DIR) not in sys.path:
    sys.path.append(str(_N0B_DIR))

from commands.speak_voices import (  # noqa: E402
    POCKET_BY_ID,
    POCKET_MODEL_BYTES,
    POCKET_MODEL_FILE,
    POCKET_MODEL_REPO,
    POCKET_MODEL_REVISION,
    POCKET_MODEL_SHA256,
    POCKET_TOKENIZER_BYTES,
    POCKET_TOKENIZER_FILE,
    POCKET_TOKENIZER_REVISION,
    POCKET_TOKENIZER_SHA256,
    chunk_text,
    pocket_cache_path,
)

SAMPLE_RATE = 24000
MAX_CHUNK_CHARS = 300
MAX_CHUNK_SECONDS = 60
THREADS = 2


@dataclass(frozen=True)
class _Asset:
    revision: str
    name: str
    sha256: str
    size: int


def _assets_for(voice_id: str) -> list[_Asset]:
    voice = POCKET_BY_ID[voice_id]
    return [
        _Asset(
            POCKET_MODEL_REVISION,
            POCKET_MODEL_FILE,
            POCKET_MODEL_SHA256,
            POCKET_MODEL_BYTES,
        ),
        _Asset(
            POCKET_TOKENIZER_REVISION,
            POCKET_TOKENIZER_FILE,
            POCKET_TOKENIZER_SHA256,
            POCKET_TOKENIZER_BYTES,
        ),
        _Asset(
            POCKET_MODEL_REVISION,
            voice.pocket_embedding,
            voice.sha256,
            voice.size,
        ),
    ]


def _file_sha256(path: Path) -> str:
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _good(path: Path, asset: _Asset) -> bool:
    try:
        if not path.is_file() or path.stat().st_size != asset.size:
            return False
        return _file_sha256(path) == asset.sha256
    except OSError:
        return False


def _discard(hf_home: Path, asset: _Asset) -> None:
    link = pocket_cache_path(hf_home, asset.revision, asset.name)
    blobs = (link.parents[len(Path(asset.name).parts) + 1] / "blobs").resolve()
    try:
        target = link.resolve() if link.is_symlink() else None
        link.unlink()
        if target is not None and target.parent == blobs:
            target.unlink()
    except OSError:
        pass


def ensure_verified_assets(hf_home: Path, voice_id: str) -> None:
    from huggingface_hub import hf_hub_download

    cache_dir = hf_home / "hub"
    for asset in _assets_for(voice_id):
        path = pocket_cache_path(hf_home, asset.revision, asset.name)
        if _good(path, asset):
            continue
        _discard(hf_home, asset)
        print(f"downloading {asset.name}...", file=sys.stderr)
        hf_hub_download(
            repo_id=POCKET_MODEL_REPO,
            filename=asset.name,
            revision=asset.revision,
            cache_dir=cache_dir,
        )
        path = pocket_cache_path(hf_home, asset.revision, asset.name)
        if not _good(path, asset):
            _discard(hf_home, asset)
            raise RuntimeError(
                f"downloaded {asset.name} failed pinned SHA-256 verification"
            )


def load_pocket_model(hf_home: Path):
    import torch
    import yaml
    from pocket_tts import TTSModel
    from pocket_tts.utils.config import CONFIGS_DIR

    config = yaml.safe_load(
        (CONFIGS_DIR / "english.yaml").read_text(encoding="utf-8")
    )
    wanted = (
        f"hf://{POCKET_MODEL_REPO}/{POCKET_TOKENIZER_FILE}"
        f"@{POCKET_TOKENIZER_REVISION}"
    )
    if config["flow_lm"]["lookup_table"]["tokenizer_path"] != wanted:
        raise RuntimeError(
            "installed pocket-tts references an unexpected tokenizer revision"
        )
    pinned = f"hf://{POCKET_MODEL_REPO}/{POCKET_MODEL_FILE}@{POCKET_MODEL_REVISION}"
    config["weights_path"] = pinned
    config["weights_path_without_voice_cloning"] = pinned
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "english.yaml"
        cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        model = TTSModel.load_model(config=str(cfg_path))
    torch.set_num_threads(THREADS)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass
    if model.sample_rate != SAMPLE_RATE:
        raise RuntimeError(f"unexpected Pocket TTS sample rate {model.sample_rate}")
    return model


def synth_chunk(model, state, text: str) -> bytes:
    import torch

    stop = threading.Event()
    tensors = []
    total = 0
    # Pocket TTS mutates streaming state from a background thread, which forbids inference_mode.
    with torch.no_grad():
        for chunk in model.generate_audio_stream(
            state, text, copy_state=True, stop=stop
        ):
            total += int(chunk.shape[-1])
            if total > MAX_CHUNK_SECONDS * SAMPLE_RATE:
                stop.set()
                raise RuntimeError(
                    f"chunk exceeded {MAX_CHUNK_SECONDS}s synthesis guard"
                )
            tensors.append(chunk.reshape(-1))
        if not tensors:
            raise RuntimeError("Pocket TTS produced no audio")
        audio = torch.cat(tensors).clamp(-1.0, 1.0)
    return (audio * 32767.0).round().to(torch.int16).numpy().astype("<i2").tobytes()


def main(argv: list[str]) -> int:
    manifest_path, out_path, voice_id = argv[:3]
    if voice_id not in POCKET_BY_ID:
        print(
            f"n0b ai speak: unknown Pocket voice {voice_id!r}", file=sys.stderr
        )
        return 2
    pieces = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    hf_home = Path(
        os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface")
    )
    os.environ["HF_HOME"] = str(hf_home)
    os.environ["HF_HUB_CACHE"] = str(hf_home / "hub")

    try:
        ensure_verified_assets(hf_home, voice_id)
        os.environ["HF_HUB_OFFLINE"] = "1"
        model = load_pocket_model(hf_home)
        emb_path = pocket_cache_path(
            hf_home, POCKET_MODEL_REVISION, POCKET_BY_ID[voice_id].pocket_embedding
        )
        state = model.get_state_for_audio_prompt(emb_path)
        pcm_parts: list[bytes] = []
        for i, piece in enumerate(pieces):
            text = (piece.get("text") or "").strip()
            silence_after = float(piece.get("silence_after", 0.0))
            if text:
                for j, sub in enumerate(chunk_text(text, MAX_CHUNK_CHARS)):
                    pcm_parts.append(synth_chunk(model, state, sub))
                    print(f"  piece {i + 1}.{j + 1}", file=sys.stderr)
            if silence_after > 0:
                frames = max(0, int(round(SAMPLE_RATE * silence_after)))
                if frames:
                    pcm_parts.append(b"\x00\x00" * frames)
                    print(f"  silence {silence_after:.2f}s", file=sys.stderr)
        pcm = b"".join(pcm_parts)
        if not pcm:
            print("no audio produced", file=sys.stderr)
            return 1
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm)
        return 0
    except Exception as exc:
        print(f"n0b ai speak: Pocket TTS failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
