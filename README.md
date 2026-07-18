# Neil's Bin Here

These are my common command-line utilities. Install by `source`'ing `install.sh` after cloning.

## Note on Shebangs

All bash scripts use `#!/usr/bin/env bash` instead of `#!/bin/bash`. This allows the scripts to use a newer version of bash if installed (e.g., via Homebrew on macOS) rather than being forced to use the system's outdated bash 3.2.57.

## Commands

| Command | Description |
|---------|-------------|
| `n0b` | Kitchen-sink utilities — `n0b json`, `n0b az tail`, `n0b ai video`, etc. ([apps/n0b/README.md](apps/n0b/README.md)) |
| `h` | Highlight text patterns in color by piping ([docs](docs/h.md)) |
| `install.sh` | Add ~/bin directory to PATH |
| `NMP.py` | [Neil's Manual Proxy](docs/NMP.md) |

## Claude Code Skills

Top-level tool docs under `docs/` and `apps/n0b/docs/` can be installed as agent skills with
`source ~/bin/install.sh --skills` (Claude Code and Cursor). a8s agent skills
(`tell`, etc.) install per-project via `a8s install` from the agent directory.
