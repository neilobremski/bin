# Requirements

Single source of truth for the shared repo venv at `~/bin/.venv`.

Each file is one **group**. `lib/venv_util.py` installs a group on first use;
any app in this repo can call `ensure_group("<name>")` so pip deps stay in one
place and versions do not fight across per-app venvs.

| File | Used by |
|------|---------|
| `ai-torch-cpu.txt` / `ai-torch-cuda.txt` | `n0b ai image`, `n0b ai transcribe` (torch picked by GPU) |
| `ai.txt` | all `n0b ai` local inference (image, kokoro speak, whisper) |
| `dev.txt` | pytest for repo tests |
| `b3t.txt` | `apps/b3t` |
| `a8s-test.txt` | `apps/a8s` MQTT transport tests |
| `r4t.txt` | `apps/r4t` Textual chat TUI |
| `audio.txt` | `n0b ai audio` (AudioLDM, Bark) |

Add new deps here, not inline `pip install` in app code. If two apps need the
same package, put it in one group file (or a shared file) with one version pin.
