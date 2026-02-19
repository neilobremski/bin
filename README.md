# Neil's Bin Here

These are my common command-line utilities. Install by `source`'ing `install.sh` after cloning.

## Note on Shebangs

All bash scripts use `#!/usr/bin/env bash` instead of `#!/bin/bash`. This allows the scripts to use a newer version of bash if installed (e.g., via Homebrew on macOS) rather than being forced to use the system's outdated bash 3.2.57.

## Commands

| Command | Description |
|---------|-------------|
| `az-pr-describe` | Set the description of an Azure DevOps Pull Request with markdown ([docs](docs/az-pr-describe.md)) |
| `az-pr-dump` | Dump an Azure DevOps Pull Request (metadata, threads, iterations) to JSON ([docs](docs/az-pr-dump.md)) |
| `az-wi-comment` | Add a Markdown comment with inline images to an Azure DevOps work item ([docs](docs/az-wi-comment.md)) |
| `aztail` | Tail Azure web app logs by environment alias (dev, qa, staging, prod) |
| `h` | Highlight text patterns in color by piping ([docs](docs/h.md)) |
| `install.sh` | Add ~/bin directory to PATH |
| `ltx-video` | Generate videos using LTX-Video (2B model, 3s @ 24fps default) ([docs](docs/ltx-video.md)) |
| `NMP.py` | [Neil's Manual Proxy](docs/NMP.md) |
| `py-json-tool` | Pretty-print and validate JSON via Python ([docs](docs/py-json-tool.md)) |
| `speak` | Cross-platform text-to-speech (macOS `say`, `spd-say`, `espeak`, WSL PowerShell) |

## Claude Code Skills

When `install.sh` detects Claude Code (`~/.claude`), it automatically installs each
documented tool as a [skill](https://docs.anthropic.com/en/docs/claude-code/skills).
Use `/az-pr-dump`, `/payi`, etc. in Claude Code to get tool-aware assistance.
