# n0b — Neil's Bin kitchen-sink CLI

Unified namespace for small utilities. Stdlib-only Python 3.x for the core CLI; no pip deps required to run `n0b` itself.

## Entry points

| Platform | Path |
|----------|------|
| Unix / macOS / Git Bash | `~/bin/n0b` (polyglot bash + PowerShell → `apps/n0b/n0b.py`) |
| Windows cmd | `~/bin/n0b.cmd` |

```bash
n0b --help
n0b json --help   # per-group help where implemented
```

## Documentation index

Detailed docs live in [`docs/`](docs/):

| Doc | Command | Summary |
|-----|---------|---------|
| [json.md](docs/json.md) | `n0b json` | Pretty-print JSON via stdlib `json.tool` |
| [ltx-video.md](docs/ltx-video.md) | `n0b ai video` | LTX-Video generation, models, setup guide |
| [research.md](docs/research.md) | `n0b ai research` | OpenAI o4-mini-deep-research |
| [secrets.md](docs/secrets.md) | `n0b secrets` | Get/set secrets — env, `~/lib`, Keychain, dotenv |
| [ai-speak.md](docs/ai-speak.md) | `n0b ai speak` | Text-to-speech — macOS `say` or offline Kokoro |
| [transcribe.md](docs/transcribe.md) | `n0b ai transcribe` | Local Whisper speech-to-text, hints + replacement files |
| [quota.md](docs/quota.md) | `n0b quota` | Live AI tool rate limits (Antigravity / `agy`) |

### Quick reference (no separate doc yet)

| Group | Subcommands | Notes |
|-------|-------------|-------|
| `az` | `tail <env>` | Azure webapp log tail — env aliases: `dev`, `qa`, `staging`, `prod` |
| `ports` | `free`, `listen <port>` | Ephemeral free port; list process on a port (`lsof` / `netstat`) |
| `gpu` | `cuda`, `mps`, `mlx`, `mb-free` | GPU / MLX checks; free MiB |
| `mqtt` | `pub`, `sub` | `mosquitto_*` with `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASS` |
| `ai` | `image`, `video`, `audio`, `research`, `speak`, `transcribe` | See [ltx-video.md](docs/ltx-video.md) for video; [research.md](docs/research.md) for deep research; [ai-speak.md](docs/ai-speak.md) for text-to-speech; [transcribe.md](docs/transcribe.md) for speech-to-text |
| `video` | `last-frame` | Extract last frame with `ffmpeg` |
| `quota` | `[agy]` | Live AI tool quotas (`agy` = Antigravity language-server API) |

### AI model defaults

| Subcommand | Default backend | Override |
|------------|-----------------|----------|
| `n0b ai image` | Z-Image (`z-image.sh`) | `--model z-image` |
| `n0b ai video` | LTX-Video 1, LTX-2 (PyTorch), MLX-Video (Apple Silicon) | `--model ltx-2`, `--model ltx-1`, or `-2`/`-1` flags |
| `n0b ai audio` | AudioLDM (`audioldm.sh`) | `--model bark` for Suno Bark |
| `n0b ai speak` | macOS `say` (auto), Kokoro offline | `--engine`, `--voice`, `-o` |
| `n0b ai transcribe` | Whisper `turbo` (local, no API key) | `--model tiny|base|small|medium|large` |

## Layout

```
apps/n0b/
├── n0b.py          entry point
├── cli.py          argparse dispatch
├── commands/       per-group implementations
├── scripts/        AI wrapper bash scripts
├── docs/           user + skill-installable docs (this index links them)
└── tests/
```

## Agent skills

Docs under `docs/` with YAML frontmatter install as Claude/Cursor skills via:

```bash
source ~/bin/install.sh --skills
```

(`install.sh` scans both `~/bin/docs/` and `~/bin/apps/n0b/docs/`.)

## Tests

```bash
python3 -m pytest apps/n0b/tests/
```
