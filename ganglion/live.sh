#!/usr/bin/env bash
# Ganglion — thin launcher. The real work is in ganglion.py.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$DIR/ganglion.py"
