# Neil's Bin Here

These are my common command-line utilities. Install by `source`'ing `install.sh` after cloning.

On Windows, put this directory on `PATH` and PowerShell finds the `.ps1` shims
on its own — `ark`, `a8s`, `tell`, `tells`, `r4t`, `k7e`, and `n0b` all run
without typing the extension. The matching `.cmd` shims cover `cmd.exe`.

New to the Ark Suite (a8s + r4t + k7e)? Start with **[The Ark Raising](guides/README.md)** — a chapter-by-chapter build-along that raises a crew of AI agents from nothing.

## Note on Shebangs

All bash scripts use `#!/usr/bin/env bash` instead of `#!/bin/bash`. This allows the scripts to use a newer version of bash if installed (e.g., via Homebrew on macOS) rather than being forced to use the system's outdated bash 3.2.57.

## Commands

| Command | Description |
|---------|-------------|
| `ark` | Front door to the Ark Suite — suite status and `ark doctor` prereq probes ([docs](docs/ark.md)) |
| `n0b` | Kitchen-sink utilities — `n0b json`, `n0b az tail`, `n0b ai video`, etc. ([apps/n0b/README.md](apps/n0b/README.md)) |
| `h` | Highlight text patterns in color by piping ([docs](docs/h.md)) |
| `install.sh` | Add ~/bin directory to PATH |
| `NMP.py` | [Neil's Manual Proxy](docs/NMP.md) |

## Claude Code Skills

Top-level tool docs under `docs/` and `apps/n0b/docs/` can be installed as agent skills with
`source ~/bin/install.sh --skills` (Claude Code and Cursor).
