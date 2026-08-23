# Requirements

Single source of truth for the shared Python substrate at `~/bin/.venv`.

Each file is one **group**. `lib/venv_util.py` installs a group on first use;
any app in this repo can call `ensure_group("<name>")`. A clean venv lives at
`.venv/runtime`; packages land in `.venv/groups/<abi-platform>/<group>` and are
added only to the child process that needs them. Apps share a group without
letting unrelated groups overwrite each other's versions.

Installs use `uv pip --target` when available, fall back to the runtime's pip,
and swap a completed temporary directory into place atomically. The requirement
file hash invalidates an old group. A Python ABI or platform change selects a
new directory, while a broken Homebrew venv symlink rebuilds only the clean
runtime and reuses compatible groups. The bootstrap prefers a uv-managed Python
3.12 when available, then falls back to compatible interpreters on `PATH`.

| File | Used by |
|------|---------|
| `ai-torch-cpu.txt` / `ai-torch-cuda.txt` | `n0b ai image`, `n0b ai transcribe` (torch picked by GPU) |
| `ai.txt` | image generation, Kokoro speech, and standard Whisper |
| `ai-mlx.txt` | Apple Silicon STT (`mlx-whisper`, `parakeet-mlx`) for `n0b ai transcribe` |
| `dev.txt` | pytest for repo tests |
| `b3t.txt` | `apps/b3t` |
| `audio.txt` | `n0b ai audio` (AudioLDM, Bark) |

Add new deps here, not inline `pip install` in app code. If two apps need the
same package, put it in one group file (or a shared file) with one version pin.

Run commands in a group with:

```bash
python3 lib/venv_exec.py dev -- -m pytest apps/n0b/tests/
```
