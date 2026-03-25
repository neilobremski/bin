#!/usr/bin/env bash
# PFC — thin launcher. The real work is in pfc.py.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$DIR/pfc.py"
